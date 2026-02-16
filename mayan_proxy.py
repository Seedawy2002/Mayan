import http.server
import socketserver
import requests
import json
import logging
import sys

import os

# Configuration
PORT = int(os.environ.get("PROXY_PORT", 8075))
TARGET_URL = os.environ.get("TARGET_URL", "http://mayan-app:8000")
# DB config (match Mayan's) for TrashedDocumentDeletedInfo lookup
DB_HOST = os.environ.get("DB_HOST", "localhost")
DB_PORT = int(os.environ.get("DB_PORT", 5432))
DB_NAME = os.environ.get("DB_NAME", "mayan")
DB_USER = os.environ.get("DB_USER", "postgres")
DB_PASSWORD = os.environ.get("DB_PASSWORD", "")
TARGET_URLS = [
    TARGET_URL.rstrip('/'), 
    "http://mayan-app:8000", 
]

# Setup logging
logging.basicConfig(
    level=logging.INFO,
    format='%(asctime)s - %(levelname)s - %(message)s',
    handlers=[logging.StreamHandler(sys.stdout)]
)
logger = logging.getLogger("MayanProxy")


def _get_document_id_for_event(event_id):
    """Look up document_id from TrashedDocumentDeletedInfo by event_id. Returns str or None."""
    if event_id is None:
        return None
    try:
        import psycopg2
        conn = psycopg2.connect(
            host=DB_HOST, port=DB_PORT, dbname=DB_NAME, user=DB_USER, password=DB_PASSWORD
        )
        cur = conn.cursor()
        cur.execute(
            "SELECT document_id FROM events_document_id_fix_trasheddocumentdeletedinfo WHERE event_id = %s LIMIT 1",
            (int(event_id),),
        )
        row = cur.fetchone()
        cur.close()
        conn.close()
        return str(row[0]) if row else None
    except Exception as e:
        logger.debug("DB lookup failed for event %s: %s", event_id, e)
        return None


class MayanProxyHandler(http.server.BaseHTTPRequestHandler):
    def log_message(self, format, *args):
        # Suppress default http.server logging to use our own
        return

    def do_GET(self):
        self._proxy_request("GET")

    def do_POST(self):
        self._proxy_request("POST")

    def do_PUT(self):
        self._proxy_request("PUT")

    def do_PATCH(self):
        self._proxy_request("PATCH")

    def do_DELETE(self):
        self._proxy_request("DELETE")

    def _proxy_request(self, method):
        url = f"{TARGET_URL}{self.path}"
        headers = {key: value for key, value in self.headers.items() if key.lower() != 'host'}
        
        # Read body if present
        content_length = int(self.headers.get('Content-Length', 0))
        body = self.rfile.read(content_length) if content_length > 0 else None

        try:
            # Forward the request to Mayan
            response = requests.request(
                method=method,
                url=url,
                headers=headers,
                data=body,
                allow_redirects=False,
                stream=True # Stream response to handle large files
            )

            # Determine if we should intercept and fix JSON
            # We only care about the events API and successful JSON responses
            is_events_api = "/api/v4/events/" in self.path
            should_fix = is_events_api and response.status_code == 200
            
            if should_fix:
                content = response.content # Fully load for JSON fixing
                try:
                    data = json.loads(content)
                    if self._fix_data(data):
                        fixed_content = json.dumps(data, ensure_ascii=False).encode('utf-8')
                        self._send_proxied_response(response, fixed_content, headers_mod={'X-Mayan-Fix': 'Applied', 'Content-Length': str(len(fixed_content))})
                        logger.info(f"FIXED: {method} {self.path}")
                        return
                    else:
                        self._send_proxied_response(response, content)
                        logger.info(f"PASS: {method} {self.path} (No fix needed)")
                        return
                except Exception as e:
                    logger.error(f"Error parsing/fixing JSON: {e}")
                    self._send_proxied_response(response, content)
            else:
                # Transparently pass through everything else
                self._send_proxied_response(response)
                if is_events_api:
                    logger.info(f"PROXY: {method} {self.path} ({response.status_code})")

        except Exception as e:
            logger.error(f"Proxy connection failed: {e}")
            self.send_error(502, f"Bad Gateway: Could not connect to {TARGET_URL}")

    def _send_proxied_response(self, response, override_content=None, headers_mod=None):
        self.send_response(response.status_code)
        
        # Copy headers from original response
        for key, value in response.headers.items():
            k_low = key.lower()
            # Skip hop-by-hop and encoding headers we might have changed or handled differently
            if k_low in ('content-encoding', 'transfer-encoding', 'connection', 'keep-alive', 'proxy-authenticate', 'proxy-authorization', 'te', 'trailers', 'upgrade'):
                continue
            if headers_mod and key in headers_mod:
                continue
            # Content-Type needs to be preserved exactly
            self.send_header(key, value)
        
        # Add/Override headers
        if headers_mod:
            for key, value in headers_mod.items():
                self.send_header(key, value)
        
        self.end_headers()
        
        if override_content:
            self.wfile.write(override_content)
        else:
            # Stream the content if not overridden
            for chunk in response.iter_content(chunk_size=8192):
                if chunk:
                    self.wfile.write(chunk)

    def _fix_data(self, data):
        """Recursively find and fix 'Unable to find serializer' strings and internal URLs."""
        if not isinstance(data, (dict, list)):
            return False
        
        changed = False
        host = self.headers.get('Host', f"localhost:{PORT}")
        proto = "https" if self.headers.get('X-Forwarded-Proto') == 'https' else "http"
        proxy_base = f"{proto}://{host}"

        # 1. Broad URL Rewrite: Catch any string that looks like an internal Mayan URL
        if isinstance(data, dict):
            for k, v in list(data.items()):
                if isinstance(v, str):
                    for t_url in TARGET_URLS:
                        if t_url in v:
                            data[k] = v.replace(t_url, proxy_base)
                            changed = True
                elif isinstance(v, (dict, list)):
                    if self._fix_data(v):
                        changed = True
        
        elif isinstance(data, list):
            for i, item in enumerate(data):
                if isinstance(item, (dict, list)):
                    if self._fix_data(item):
                        changed = True
                elif isinstance(item, str):
                    for t_url in TARGET_URLS:
                        if t_url in item:
                            data[i] = item.replace(t_url, proxy_base)
                            changed = True

        # 2. Specific Event Fixes (only for dicts that look like events)
        if isinstance(data, dict) and 'verb' in data:
            # Check for trashed_document_deleted event
            verb = data.get('verb', {})
            verb_id = verb.get('id') if isinstance(verb, dict) else str(verb)
            
            if "trashed_document_deleted" in verb_id:
                # Fix 'target' field
                target = data.get('target')
                is_unable = isinstance(target, str) and "Unable to find serializer" in target
                obj_id = data.get('target_object_id')
                
                # Always create/update target dict for trashed_document_deleted
                if is_unable or not isinstance(target, dict):
                    data['target'] = {'id': int(obj_id) if obj_id is not None else None}
                    target = data['target']
                    changed = True
                    logger.debug(f"Fixed 'Unable to find serializer' for event {data.get('id')}")
                else:
                    target = data['target']
                
                # Determine document_id based on target content type
                target_ct = data.get('target_content_type', {})
                model = target_ct.get('model') if isinstance(target_ct, dict) else None
                
                if model == 'document':
                    # target_object_id IS the document id when target is Document
                    if target.get('document_id') != (int(obj_id) if obj_id is not None else None):
                        target['document_id'] = int(obj_id) if obj_id is not None else None
                        changed = True
                        logger.info(f"Event {data.get('id')}: Set document_id={obj_id} (from Document target)")
                        
                elif model == 'documenttype':
                    # target_object_id is the document TYPE id, NOT the deleted doc id
                    # Look up real document_id from TrashedDocumentDeletedInfo by event_id
                    event_id = data.get('id')
                    doc_id = _get_document_id_for_event(event_id)
                    
                    if doc_id:
                        target['document_id'] = int(doc_id)
                        target['document_type_id'] = int(obj_id) if obj_id is not None else None
                        changed = True
                        logger.info(f"Event {event_id}: Set document_id={doc_id} (from DB lookup, doc_type={obj_id})")
                    else:
                        target['document_id'] = None
                        target['document_type_id'] = int(obj_id) if obj_id is not None else None
                        changed = True
                        logger.warning(f"Event {event_id}: Could not find document_id in DB for doc_type={obj_id}")
                
                # Fix 'actor' if needed
                actor = data.get('actor')
                if isinstance(actor, str) and "Unable to find serializer" in actor:
                    actor_id = data.get('actor_object_id')
                    data['actor'] = {'id': int(actor_id) if actor_id is not None else None}
                    changed = True
            else:
                # For non-trashed_document_deleted events, still fix "Unable to find serializer" errors
                target = data.get('target')
                is_unable = isinstance(target, str) and "Unable to find serializer" in target
                
                if is_unable:
                    obj_id = data.get('target_object_id')
                    data['target'] = {'id': int(obj_id) if obj_id is not None else None}
                    changed = True
                
                actor = data.get('actor')
                if isinstance(actor, str) and "Unable to find serializer" in actor:
                    obj_id = data.get('actor_object_id')
                    data['actor'] = {'id': int(obj_id) if obj_id is not None else None}
                    changed = True
                
        return changed

if __name__ == "__main__":
    socketserver.TCPServer.allow_reuse_address = True
    with socketserver.TCPServer(("", PORT), MayanProxyHandler) as httpd:
        print(f"\n=======================================")
        print(f"   MAYAN EVENT FIX PROXY STARTED")
        print(f"=======================================")
        print(f" Listening on: http://localhost:{PORT}")
        print(f" Forwarding to: {TARGET_URL}")
        print(f"=======================================\n")
        try:
            httpd.serve_forever()
        except KeyboardInterrupt:
            print("\nProxy shutting down...")

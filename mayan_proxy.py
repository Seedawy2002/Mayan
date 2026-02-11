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
# Broad patterns to catch any internal Mayan URL
TARGET_URLS = [
    TARGET_URL.rstrip('/'), 
    "http://mayan-app:8000", 
    "http://localhost:8000",
    "http://localhost:8070",
    "http://127.0.0.1:8000",
]

# Setup logging
logging.basicConfig(
    level=logging.INFO,
    format='%(asctime)s - %(levelname)s - %(message)s',
    handlers=[logging.StreamHandler(sys.stdout)]
)
logger = logging.getLogger("MayanProxy")

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
            # Fix 'target'
            target = data.get('target')
            is_unable = isinstance(target, str) and "Unable to find serializer" in target
            
            if is_unable or isinstance(target, dict):
                obj_id = data.get('target_object_id')
                if is_unable:
                    data['target'] = {'id': str(obj_id) if obj_id is not None else None}
                    target = data['target']
                    changed = True
                
                # Check for trashed_document_deleted to inject document_id
                verb = data.get('verb', {})
                verb_id = verb.get('id') if isinstance(verb, dict) else str(verb)
                
                if "trashed_document_deleted" in verb_id:
                    target_ct = data.get('target_content_type', {})
                    model = target_ct.get('model') if isinstance(target_ct, dict) else None
                    if model == 'document':
                        # target_object_id IS the document id when target is Document
                        target['document_id'] = str(obj_id)
                        changed = True
                    # When model is 'documenttype', target_object_id is the document TYPE id, NOT the
                    # deleted document id. Do NOT overwrite - leave document_id from Mayan's
                    # events_document_id_fix (TrashedDocumentDeletedInfo) which has the real value.
            
            # Fix 'actor'
            actor = data.get('actor')
            if isinstance(actor, str) and "Unable to find serializer" in actor:
                obj_id = data.get('actor_object_id')
                data['actor'] = {'id': str(obj_id) if obj_id is not None else None}
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

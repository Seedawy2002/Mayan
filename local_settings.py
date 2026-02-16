from mayan.settings.production import *

INSTALLED_APPS += ('events_document_id_fix.apps.EventsDocumentIdFixConfig',)

try:
    if 'LOGGING' in locals():
        if 'loggers' not in LOGGING:
            LOGGING['loggers'] = {}
        LOGGING['loggers']['events_document_id_fix'] = {
            'handlers': ['console'],
            'level': 'DEBUG',
            'propagate': True,
        }
except Exception:
    pass

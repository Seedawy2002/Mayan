from mayan.settings.production import *

INSTALLED_APPS += ('mayan_event_enrichment.apps.MayanEventEnrichmentConfig',)

try:
    if 'LOGGING' in locals():
        if 'loggers' not in LOGGING:
            LOGGING['loggers'] = {}
        LOGGING['loggers']['mayan_event_enrichment'] = {
            'handlers': ['console'],
            'level': 'DEBUG',
            'propagate': True,
        }
except Exception:
    pass

"""
Empty tasks module - ensures this app is imported when Celery autodiscovers,
so our task_prerun handler (from apps.ready) is registered in the worker process.
"""

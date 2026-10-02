import asyncio
from celery import Celery
from sqlalchemy.orm import sessionmaker
from postchief.db import make_engine
from postchief.publishing.engine import due_publications,execute_publication
from postchief.analytics.service import due_metrics,collect

def create_worker(settings, *, provider_factory=None):
    app=Celery('postchief',broker=settings.redis_url)
    app.conf.update(task_serializer='json',accept_content=['json'],result_serializer='json',task_ignore_result=True,
        task_acks_late=True,task_reject_on_worker_lost=True,worker_prefetch_multiplier=1,
        task_soft_time_limit=300,task_time_limit=330,broker_connection_retry_on_startup=True,
        broker_transport_options={'visibility_timeout':600},
        task_default_queue='postchief',timezone='UTC',enable_utc=True,
        beat_schedule={'due-publications':{'task':'postchief.dispatch','schedule':15.0},
            'due-analytics':{'task':'postchief.analytics_dispatch','schedule':60.0}})

    @app.task(name='postchief.dispatch', shared=False)
    def dispatch():
        engine=make_engine(settings.database_url)
        try:
            factory=sessionmaker(engine,expire_on_commit=False)
            for publication_id in due_publications(factory):
                # Redis failure leaves database state due for the next sweep.
                app.tasks['postchief.publish'].delay(publication_id)
        finally: engine.dispose()

    @app.task(name='postchief.publish', shared=False)
    def publish(publication_id):
        engine=make_engine(settings.database_url)
        try:
            factory=sessionmaker(engine,expire_on_commit=False)
            kwargs={'provider_factory':provider_factory} if provider_factory else {}
            asyncio.run(execute_publication(publication_id,factory,settings,**kwargs))
        finally: engine.dispose()

    @app.task(name='postchief.analytics_dispatch',shared=False)
    def analytics_dispatch():
        engine=make_engine(settings.database_url)
        try:
            for publication_id in due_metrics(sessionmaker(engine,expire_on_commit=False)):
                app.tasks['postchief.analytics'].delay(publication_id)
        finally: engine.dispose()

    @app.task(name='postchief.analytics',shared=False)
    def analytics(publication_id):
        engine=make_engine(settings.database_url)
        try:
            kwargs={'provider_factory':provider_factory} if provider_factory else {}
            asyncio.run(collect(publication_id,sessionmaker(engine,expire_on_commit=False),settings,**kwargs))
        finally: engine.dispose()

    return app

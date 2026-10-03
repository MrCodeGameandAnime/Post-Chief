import asyncio
from celery import Celery
from celery.signals import worker_ready
from sqlalchemy.orm import sessionmaker
from postchief.db import make_engine
from postchief.publishing.engine import due_publications,execute_publication
from postchief.analytics.service import due_metrics,collect
from postchief.providers.twitch_validation import due_validation,validate_connection

def create_worker(settings, *, provider_factory=None):
    app=Celery('postchief',broker=settings.redis_url)
    app.conf.update(task_serializer='json',accept_content=['json'],result_serializer='json',task_ignore_result=True,
        task_acks_late=True,task_reject_on_worker_lost=True,worker_prefetch_multiplier=1,
        task_soft_time_limit=300,task_time_limit=330,broker_connection_retry_on_startup=True,
        broker_transport_options={'visibility_timeout':600},
        task_default_queue='postchief',timezone='UTC',enable_utc=True,
        beat_schedule={'due-publications':{'task':'postchief.dispatch','schedule':15.0},
            'due-analytics':{'task':'postchief.analytics_dispatch','schedule':60.0},
            'twitch-validation':{'task':'postchief.twitch_validation_dispatch','schedule':60.0}})

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

    @app.task(name='postchief.twitch_validation_dispatch',shared=False)
    def twitch_validation_dispatch(startup=False):
        engine=make_engine(settings.database_url)
        try:
            for account_id in due_validation(sessionmaker(engine,expire_on_commit=False),startup):
                app.tasks['postchief.twitch_validate'].delay(account_id,startup)
        finally: engine.dispose()

    @app.task(name='postchief.twitch_validate',shared=False)
    def twitch_validate(account_id,startup=False):
        engine=make_engine(settings.database_url)
        try: asyncio.run(validate_connection(account_id,sessionmaker(engine,expire_on_commit=False),settings,startup))
        finally: engine.dispose()

    def validate_on_start(sender=None,**kwargs):
        if sender is not None and sender.app is app:
            app.tasks['postchief.twitch_validation_dispatch'].delay(True)
    worker_ready.connect(validate_on_start,weak=False)

    return app

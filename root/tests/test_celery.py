import os
import uuid
import pytest
from threading import Event, Lock
from celery.signals import task_postrun
from celery.contrib.testing.worker import start_worker
from redis import Redis
from postchief_worker.application import create_worker
from test_publishing import setup_campaign, FakeProvider


def test_redis_dispatch_and_duplicate_delivery_publish_once(app, client):
    url = os.environ.get('POST_CHIEF_TEST_REDIS_URL')
    if not url:
        pytest.skip('TCP Redis integration requires POST_CHIEF_TEST_REDIS_URL; mandatory in CI')
    # Every broker, binding, control and result key belongs to this exact test prefix.
    prefix = 'postchief_test_' + uuid.uuid4().hex + ':'
    redis = Redis.from_url(url)
    assert redis.ping()
    settings = app.state.settings.model_copy(update={'redis_url': url})
    class QueueProvider(FakeProvider):
        async def get_post_metrics(self,*args): return {'likes':7}
    provider = QueueProvider()
    worker = create_worker(settings, provider_factory=lambda *args: provider)
    worker.conf.update(task_default_queue=prefix+'queue',
        broker_transport_options={'global_keyprefix':prefix,'visibility_timeout':600},
        worker_enable_remote_control=False, worker_send_task_events=False)
    campaign = setup_campaign(app, client)
    publication_id = campaign['publications'][0]['id']
    assert client.post(f'/api/campaigns/{campaign["id"]}/publish').status_code == 200
    complete = Event()
    lock = Lock()
    states = []
    publication_task = worker.tasks['postchief.publish']
    def finished(sender=None, state=None, **kwargs):
        with lock:
            states.append(state)
            if len(states) == 2:
                complete.set()
    task_postrun.connect(finished, sender=publication_task, weak=False)
    metrics_complete=Event()
    metrics_states=[]
    metrics_task=worker.tasks['postchief.analytics']
    def metrics_finished(sender=None,state=None,**kwargs):
        metrics_states.append(state);metrics_complete.set()
    task_postrun.connect(metrics_finished,sender=metrics_task,weak=False)
    try:
        with start_worker(worker, pool='solo', perform_ping_check=False, loglevel='WARNING', shutdown_timeout=15):
            # Actual Redis messages exercise the production dispatcher and publication task.
            worker.tasks['postchief.dispatch'].delay()
            publication_task.delay(publication_id)
            assert complete.wait(15), 'Redis worker did not finish both deliveries'
            assert states == ['SUCCESS', 'SUCCESS']
            worker.tasks['postchief.analytics_dispatch'].delay()
            assert metrics_complete.wait(15),'Redis analytics worker did not finish collection'
            assert metrics_states==['SUCCESS']
        assert provider.calls == 1
        assert client.get(f'/api/publications/{publication_id}').json()['status'] == 'published'
        assert client.get('/api/analytics').json()[0]['latest']['metrics']['normalized']['likes']==7
    finally:
        task_postrun.disconnect(finished, sender=publication_task)
        task_postrun.disconnect(metrics_finished,sender=metrics_task)
        worker.amqp.producer_pool.force_close_all()
        worker.pool.force_close_all()
        worker.close()
        for key in redis.scan_iter(match=prefix+'*'):
            assert key.startswith(prefix.encode())
            redis.delete(key)
        redis.close()

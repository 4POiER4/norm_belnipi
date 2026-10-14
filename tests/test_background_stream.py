import json
import threading
import time
from io import BytesIO

import pytest

from belener.background_stream import BackgroundStream, JobRegistry


def await_finished(job):
    with job._condition:
        assert job._condition.wait_for(lambda: job._done, timeout=3)


def test_browser_disconnect_does_not_close_worker():
    release, started, persisted = threading.Event(), threading.Event(), threading.Event()
    job = BackgroundStream('chat')
    def producer():
        started.set()
        yield 'data: {"status":"OCR"}\n\n'
        assert release.wait(3)
        yield 'data: {"text":"Отчёт"}\n\n'
        yield 'data: [DONE]\n\n'
        persisted.set()
    job.start(producer)
    assert started.wait(3)
    response = job.stream()
    assert 'job_id' in next(response)
    response.close()
    assert job.snapshot()['state'] == 'running' and not persisted.is_set()
    release.set()
    await_finished(job)
    assert persisted.is_set() and job.snapshot()['state'] == 'completed'
    assert 'Отчёт' in ''.join(job.stream())


def test_done_is_emitted_after_persistence_not_pipeline_done():
    saved = threading.Event()
    job = BackgroundStream('chat')
    def producer():
        yield 'data: {"text":"Result"}\n\n'
        yield 'data: [DONE]\n\n'
        saved.set()
    job.start(producer)
    for frame in job.stream():
        if '[DONE]' in frame:
            assert saved.is_set()


def test_statuses_coalesced_and_failure_reported():
    job = BackgroundStream('chat')
    for i in range(10000):
        job.publish('data: ' + json.dumps({'status': str(i)}) + '\n\n')
    assert len(job._events) == 0 and job.snapshot()['status'] == '9999'
    def fail():
        raise RuntimeError('database unavailable')
        yield
    job.start(fail)
    await_finished(job)
    assert job.snapshot()['state'] == 'failed'
    assert 'Не удалось' in ''.join(job.stream())


def test_one_active_job_per_conversation():
    registry = JobRegistry()
    job = registry.create('chat')
    with pytest.raises(ValueError): registry.create('chat')
    assert registry.get('chat') is job
    registry.discard(job)
    assert registry.get('chat') is None


def test_heartbeat_waits_only_for_pending_events(monkeypatch):
    import app
    class FakeEvent:
        def __init__(self, done):
            self.done, self.waits = done, []
        def is_set(self): return self.done
        def wait(self, timeout):
            self.waits.append(timeout)
            self.done = True
            return True
    complete, pending = FakeEvent(True), FakeEvent(False)
    frames = list(app._heartbeat_while_all([complete, pending], page_count=225,
                                          budget_sec=8000, t0=time.monotonic()))
    assert len(frames) == 1
    assert complete.waits == [] and pending.waits == [12]


def test_worker_keeps_pdf_until_report_saved_after_disconnect(monkeypatch, tmp_path):
    import app
    upload = tmp_path / 'source.pdf'
    upload.write_bytes(b'test')
    release, saved = threading.Event(), threading.Event()
    def extract(*args, **kwargs):
        yield 'data: {"status":"OCR"}\n\n'
        assert release.wait(3)
        assert upload.exists()
        yield 'data: {"text":"Table result"}\n\n'
        yield 'data: [DONE]\n\n'
    class DB:
        def __enter__(self): return self
        def __exit__(self, *args): pass
        def cursor(self): return self
        def execute(self, *args): pass
        def fetchone(self): return {'id': 'chat'}
        def commit(self): pass
    def save(*args, **kwargs):
        assert upload.exists() and args[2] == 'Table result'
        saved.set()
    monkeypatch.setattr(app, 'stream_extract_pdf_normative', extract)
    monkeypatch.setattr(app, 'get_db', DB)
    monkeypatch.setattr(app, 'save_message', save)
    job = BackgroundStream('chat')
    job.start(lambda: app._normative_job_events('chat', str(upload), 'source.pdf', '.pdf',
                                               'Нормативы', 'test-model', None))
    response = job.stream(); next(response); response.close()
    assert upload.exists()
    release.set(); await_finished(job)
    assert saved.is_set() and not upload.exists()
    assert job.snapshot()['state'] == 'completed'


def test_pdf_error_still_saved_and_cleans_file(monkeypatch, tmp_path):
    import app
    upload = tmp_path / 'bad.pdf'; upload.write_bytes(b'bad')
    def fail(*args, **kwargs):
        raise ValueError('bad PDF')
        yield
    class DB:
        def __enter__(self): return self
        def __exit__(self, *args): pass
        def cursor(self): return self
        def execute(self, *args): pass
        def fetchone(self): return {'id': 'chat'}
        def commit(self): pass
    reports = []
    monkeypatch.setattr(app, 'stream_extract_pdf_normative', fail)
    monkeypatch.setattr(app, 'get_db', DB)
    monkeypatch.setattr(app, 'save_message', lambda *args, **kwargs: reports.append(args[2]))
    frames = list(app._normative_job_events('chat', str(upload), 'bad.pdf', '.pdf', 'Нормативы', 'test', None))
    assert reports and 'Ошибка обработки' in reports[0]
    assert not upload.exists() and any('error' in frame for frame in frames)


def test_post_close_and_reconnect_does_not_restart_processing(monkeypatch, tmp_path):
    import app
    release = threading.Event()
    calls, saved = [], []
    def producer(*args, **kwargs):
        assert kwargs['normacs_requested'] is True
        calls.append(args)
        yield 'data: {"status":"OCR"}\n\n'
        assert release.wait(3)
        saved.append('saved')
        # Этот mock владеет загруженным файлом так же, как production producer.
        import os
        os.unlink(args[1])
        yield 'data: {"text":"Saved result"}\n\n'
    registry = JobRegistry()
    monkeypatch.setattr(app, '_normative_jobs', registry)
    monkeypatch.setattr(app, '_normative_job_events', producer)
    monkeypatch.setattr(app, 'ensure_upload_temp_dir', lambda: str(tmp_path))
    monkeypatch.setattr(app, 'save_message', lambda *args, **kwargs: None)
    client = app.app.test_client()
    response = client.post('/api/conversations/chat/chat', data={
        'mode': 'gost', 'file': (BytesIO(b'pdf'), 'big.pdf')}, buffered=False)
    assert response.status_code == 200
    response.close()
    job = registry.get('chat')
    assert job and client.get('/api/conversations/chat/job').json['job']['state'] == 'running'
    duplicate = client.post('/api/conversations/chat/chat', data={
        'mode': 'gost', 'file': (BytesIO(b'pdf'), 'big.pdf')})
    assert duplicate.status_code == 409
    release.set(); await_finished(job)
    response = client.get('/api/conversations/chat/job/stream')
    assert response.status_code == 200 and b'Saved result' in response.data
    assert len(calls) == 1 and saved == ['saved']
    assert not list(tmp_path.iterdir())


@pytest.mark.parametrize('filename',['small.pdf','scan.png'])
@pytest.mark.parametrize('flag',[None,'0','1'])
def test_normacs_is_default_and_can_be_disabled_for_diagnostics(monkeypatch,tmp_path,filename,flag):
    import app,os
    registry=JobRegistry(); recorded=[]
    def producer(*args,**kwargs):
        recorded.append(kwargs['normacs_requested'])
        os.unlink(args[1])
        yield 'data: {"text":"Report"}\n\n'
    monkeypatch.setattr(app,'_normative_jobs',registry)
    monkeypatch.setattr(app,'_normative_job_events',producer)
    monkeypatch.setattr(app,'save_message',lambda *args,**kwargs:None)
    monkeypatch.setattr(app,'ensure_upload_temp_dir',lambda:str(tmp_path))
    data={'mode':'gost','file':(BytesIO(b'file'),filename)}
    if flag is not None:data['normacs']=flag
    response=app.app.test_client().post('/api/conversations/test/chat',data=data,buffered=False)
    assert response.status_code==200
    response.close();await_finished(registry.get('test'))
    assert recorded==[flag!='0'] and not list(tmp_path.iterdir())

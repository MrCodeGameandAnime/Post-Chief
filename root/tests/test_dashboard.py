from fastapi.testclient import TestClient
from postchief.main import create_app


def test_dashboard_static_files_do_not_replace_api_routes(app,tmp_path):
    web=tmp_path/'web'
    web.mkdir()
    (web/'index.html').write_text('<title>Post Chief</title>')
    dashboard=create_app(app.state.settings.model_copy(update={'web_dir':str(web)}))
    with TestClient(dashboard) as client:
        assert client.get('/').text=='<title>Post Chief</title>'
        assert client.get('/api/health').json()['status']=='ok'
        assert client.get('/').headers['X-Frame-Options']=='DENY'
        assert client.get('/api/campaigns').status_code==401

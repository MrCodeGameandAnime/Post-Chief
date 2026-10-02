import warnings
import pytest
from starlette.exceptions import StarletteDeprecationWarning


MESSAGE='Using `httpx` with `starlette.testclient` is deprecated; install `httpx2` instead.'


def test_application_deprecations_fail_even_when_the_message_matches_the_exception():
    with pytest.raises(DeprecationWarning):
        warnings.warn_explicit('Deprecated Post Chief behavior',DeprecationWarning,filename='postchief/app.py',lineno=1,module='postchief.app')
    with pytest.raises(StarletteDeprecationWarning):
        warnings.warn_explicit(MESSAGE,StarletteDeprecationWarning,filename='postchief/app.py',lineno=1,module='postchief.app')


def test_only_exact_third_party_warning_is_allowed():
    warnings.warn_explicit(MESSAGE,StarletteDeprecationWarning,filename='fastapi/testclient.py',lineno=1,module='fastapi.testclient')
    with pytest.raises(StarletteDeprecationWarning):
        warnings.warn_explicit('A different Starlette deprecation',StarletteDeprecationWarning,filename='fastapi/testclient.py',lineno=1,module='fastapi.testclient')


def test_temporary_state_is_under_designated_runtime_directory(tmp_path,pytestconfig):
    from pathlib import Path
    root=Path(pytestconfig.rootpath).resolve()
    assert tmp_path.resolve().is_relative_to(root)
    assert not tmp_path.resolve().is_relative_to(root/'src')


def test_application_settings_do_not_inherit_live_environment(monkeypatch,request):
    monkeypatch.setenv('PUBLIC_URL','https://live.example.test')
    monkeypatch.setenv('META_CLIENT_SECRET','live-secret-must-not-reach-tests')
    app=request.getfixturevalue('app')
    assert app.state.settings.public_url=='http://localhost:8000'
    assert app.state.settings.meta_client_secret.get_secret_value()==''

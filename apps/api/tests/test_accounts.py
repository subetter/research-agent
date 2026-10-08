import asyncio
import hashlib
import json
import time
import pytest
import httpx
from httpx import AsyncClient, ASGITransport
from app.config import Settings
from app.main import create_app
from app.store import Store
from app.accounts import COOKIE

PASSWORD = 'test-password-123'

async def register(client, name):
    r = await client.post('/api/auth/register', json={'username':name,'display_name':name,'password':PASSWORD,'role':'admin'})
    assert r.status_code == 201, r.text
    return r.json()['user']

@pytest.fixture
async def clients(tmp_path):
    app = create_app(Settings(_env_file=None,data_dir=str(tmp_path),research_mode='demo'))
    async with app.router.lifespan_context(app):
        transport = ASGITransport(app=app)
        async with AsyncClient(transport=transport,base_url='http://testserver') as admin, AsyncClient(transport=transport,base_url='http://testserver') as user, AsyncClient(transport=transport,base_url='http://testserver') as anonymous:
            yield app, admin, user, anonymous

async def test_roles_sessions_password_and_rate_limit(clients):
    app, admin, user, anonymous = clients
    assert (await anonymous.get('/api/projects')).status_code == 401
    assert (await anonymous.get('/api/auth/bootstrap')).json()['needs_admin']
    a = await register(admin,'administrator'); b = await register(user,'member')
    assert a['role'] == 'admin' and b['role'] == 'user' and 'password_hash' not in a
    stored = app.state.store.query('SELECT password_hash FROM users WHERE id=?',(a['id'],))[0]['password_hash']
    assert stored.startswith('scrypt$') and PASSWORD not in stored
    token = admin.cookies.get(COOKIE)
    assert app.state.store.query('SELECT token_hash FROM sessions WHERE user_id=?',(a['id'],))[0]['token_hash'] == hashlib.sha256(token.encode()).hexdigest()
    assert (await anonymous.post('/api/auth/register',json={'username':'MEMBER','display_name':'repeat','password':PASSWORD})).status_code == 409
    assert (await user.get('/api/admin/users')).status_code == 403
    await admin.post('/api/auth/logout')
    assert (await admin.get('/api/auth/me')).status_code == 401
    anonymous.cookies.set(COOKIE,token)
    assert (await anonymous.get('/api/auth/me')).status_code == 401
    r = await admin.post('/api/auth/login',json={'username':'administrator','password':PASSWORD})
    assert r.status_code == 200 and 'HttpOnly' in r.headers['set-cookie'] and 'SameSite=strict' in r.headers['set-cookie']
    for _ in range(5):
        assert (await anonymous.post('/api/auth/login',json={'username':'member','password':'incorrect'})).status_code == 401
    assert (await anonymous.post('/api/auth/login',json={'username':'member','password':PASSWORD})).status_code == 429
    with app.state.store.lock, app.state.store.conn:
        app.state.store.conn.execute('UPDATE sessions SET expires_at=?',(time.time()-1,))
    assert (await user.get('/api/auth/me')).status_code == 401

async def test_conversation_context_idempotency_and_admin_records(clients):
    app, admin, user, _ = clients
    await register(admin,'administrator'); member = await register(user,'member')
    chat = (await user.post('/api/conversations',json={})).json()
    url = f"/api/conversations/{chat['id']}/messages"
    first = {'content':'第一条问题','client_id':'request-0001'}
    r = await user.post(url,json=first)
    assert r.status_code == 200, r.text
    assert len(r.json()['messages']) == 2
    replay = await user.post(url,json=first)
    assert replay.json()['replayed'] and len(replay.json()['messages']) == 2
    assert (await user.post(url,json={**first,'content':'不同消息'})).status_code == 409
    second = await user.post(url,json={'content':'继续讨论','client_id':'request-0002'})
    assert len(second.json()['messages']) == 4
    assert '第一条问题' in second.json()['messages'][-1]['content']
    assert second.json()['conversation']['title'] == '第一条问题'
    assert len((await user.get('/api/conversations')).json()) == 1
    assert (await admin.get('/api/conversations')).json() == []
    assert (await admin.get(f"/api/conversations/{chat['id']}")).status_code == 404
    assert (await admin.post(url,json=first)).status_code == 404
    p = (await user.post('/api/projects',json={'name':'成员项目'})).json()
    run = app.state.store.create_run(p['id'],'成员研究问题','demo',{})
    listing = await admin.get(f"/api/admin/records?user_id={member['id']}&limit=1")
    assert listing.status_code == 200, listing.text
    assert listing.json()['total'] == 2 and len(listing.json()['items']) == 1
    assert (await admin.get('/api/admin/records?q=第一条')).json()['total'] == 1
    detail = await admin.get(f"/api/admin/records/chat/{chat['id']}")
    assert len(detail.json()['messages']) == 4 and detail.json()['record']['username'] == 'member'
    detail = await admin.get(f"/api/admin/records/research/{run['id']}")
    assert detail.status_code == 200 and detail.json()['record']['question'] == '成员研究问题'
    users = (await admin.get('/api/admin/users')).json()
    assert users['total'] == 2 and 'password_hash' not in json.dumps(users)
    assert (await admin.get('/api/admin/overview')).json()['messages'] == 4
    assert {'chat.read','research.read','records.list'} <= {e['action'] for e in (await admin.get('/api/admin/audit')).json()}
    assert (await user.get('/api/admin/records')).status_code == 403
    assert (await admin.post(f"/api/admin/records/chat/{chat['id']}",json={})).status_code == 405

async def test_research_resource_isolation(clients):
    app, admin, user, _ = clients
    await register(admin,'administrator'); await register(user,'member')
    p = (await admin.post('/api/projects',json={'name':'管理员私有研究'})).json()
    run = app.state.store.create_run(p['id'],'私有研究问题','demo',{})
    assert (await user.get('/api/projects')).json() == []
    for suffix in ['', '/runs', '/documents']:
        assert (await user.get(f"/api/projects/{p['id']}{suffix}")).status_code == 404
    for suffix in ['', '/events', '/trace', '/evidence', '/export?format=csv']:
        assert (await user.get(f"/api/runs/{run['id']}{suffix}")).status_code == 404
    assert (await user.post(f"/api/runs/{run['id']}/commands",json={'action':'cancel','expected_revision':0})).status_code == 404
    assert (await user.post(f"/api/projects/{p['id']}/documents",files={'file':('a.txt',b'private')})).status_code == 404
    assert (await user.put(f"/api/runs/{run['id']}/plan",json={})).status_code == 404

async def test_legacy_projects_migrate_only_to_first_admin(tmp_path):
    store = Store(tmp_path/'workbench.sqlite'); project = store.create_project('历史项目',''); store.close()
    app = create_app(Settings(_env_file=None,data_dir=str(tmp_path)))
    async with app.router.lifespan_context(app):
        async with AsyncClient(transport=ASGITransport(app=app),base_url='http://testserver') as client:
            admin = await register(client,'first_admin')
            assert app.state.store.project(project['id'])['owner_id'] == admin['id']
            await register(client,'second_user')
            assert (await client.get('/api/projects')).json() == []

async def test_live_chat_uses_context_without_search_key(tmp_path,monkeypatch):
    settings = Settings(_env_file=None,data_dir=str(tmp_path),research_mode='live',llm_api_key='test-key',llm_model='test-model',tavily_api_key='')
    app = create_app(settings); requests = []; real_client = httpx.AsyncClient
    def model(request):
        requests.append(json.loads(request.content))
        return httpx.Response(200,json={'choices':[{'message':{'content':'模型测试回复'}}],'usage':{'total_tokens':12}})
    monkeypatch.setattr('app.llm.httpx.AsyncClient',lambda **kw:real_client(transport=httpx.MockTransport(model),**kw))
    async with app.router.lifespan_context(app):
        async with real_client(transport=ASGITransport(app=app),base_url='http://testserver') as client:
            await register(client,'administrator'); chat = (await client.post('/api/conversations',json={})).json()
            for i in range(2):
                r = await client.post(f"/api/conversations/{chat['id']}/messages",json={'content':f'问题{i}','client_id':f'request-{i:04}'})
                assert r.status_code == 200 and r.json()['messages'][-1]['tokens'] == 12
            assert [m['role'] for m in requests[1]['messages']] == ['system','user','assistant','user']
            assert requests[1]['messages'][1]['content'] == '问题0'
            assert requests[1]['model'] == 'test-model'

async def test_pending_conversation_rejects_parallel_turns(clients):
    _, admin, _, _ = clients
    await register(admin,'administrator'); chat = (await admin.post('/api/conversations',json={})).json()
    url = f"/api/conversations/{chat['id']}/messages"
    task = asyncio.create_task(admin.post(url,json={'content':'第一条消息','client_id':'request-first'}))
    await asyncio.sleep(.1)
    assert (await admin.post(url,json={'content':'并发消息','client_id':'request-second'})).status_code == 409
    assert len((await task).json()['messages']) == 2

async def test_sessions_and_conversations_survive_restart(tmp_path):
    settings = Settings(_env_file=None,data_dir=str(tmp_path),research_mode='demo')
    app = create_app(settings)
    async with AsyncClient(transport=ASGITransport(app=app),base_url='http://testserver') as client:
        async with app.router.lifespan_context(app):
            await register(client,'administrator')
            chat = (await client.post('/api/conversations',json={})).json()
            await client.post(f"/api/conversations/{chat['id']}/messages",json={'content':'保存这条消息','client_id':'request-persist'})
            with app.state.store.lock, app.state.store.conn:
                app.state.store.conn.execute("UPDATE chat_messages SET status='pending',content='' WHERE role='assistant'")
        async with app.router.lifespan_context(app):
            assert (await client.get('/api/auth/me')).status_code == 200
            history = (await client.get(f"/api/conversations/{chat['id']}")).json()['messages']
            assert history[0]['content'] == '保存这条消息'
            assert history[-1]['status'] == 'failed' and '重启' in history[-1]['content']

async def test_live_chat_missing_key_does_not_save_fake_answer(tmp_path):
    app = create_app(Settings(_env_file=None,data_dir=str(tmp_path),research_mode='live',llm_api_key='',llm_model=''))
    async with app.router.lifespan_context(app):
        async with AsyncClient(transport=ASGITransport(app=app),base_url='http://testserver') as client:
            await register(client,'administrator')
            chat = (await client.post('/api/conversations',json={})).json()
            r = await client.post(f"/api/conversations/{chat['id']}/messages",json={'content':'真实提问','client_id':'request-no-key'})
            assert r.status_code == 503
            assert (await client.get(f"/api/conversations/{chat['id']}")).json()['messages'] == []

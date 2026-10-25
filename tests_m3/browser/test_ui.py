"""真实浏览器 + TestClient 请求转发，不启动 HTTP 服务，不占用端口。"""
import os
from pathlib import Path
from urllib.parse import urlsplit

import pytest

pytestmark = pytest.mark.skipif(os.getenv('M3_BROWSER_TEST') != '1', reason='显式设置 M3_BROWSER_TEST=1 才运行浏览器验收')


def test_browser_workflow_and_mobile(env):
    from playwright.sync_api import sync_playwright, expect
    from conftest import PASSWORD
    from m3.worker import tick

    evidence = Path('docs/m3/evidence')
    evidence.mkdir(parents=True, exist_ok=True)
    errors = []
    with sync_playwright() as p:
        executable = os.getenv('M3_BROWSER_EXECUTABLE')
        browser = p.chromium.launch(headless=True, **({'executable_path': executable} if executable else {}))
        context = browser.new_context(viewport={'width': 1440, 'height': 1000})
        def route_request(route):
            req = route.request
            url = urlsplit(req.url)
            if url.netloc != 'm3.test':
                route.abort()
                return
            response = env.client.request(req.method, url.path + ('?' + url.query if url.query else ''),
                                          content=req.post_data_buffer, headers=req.headers)
            route.fulfill(status=response.status_code, body=response.content,
                          headers={'content-type': response.headers.get('content-type', 'text/plain')})
        context.route('**/*', route_request)
        page = context.new_page()
        page.on('pageerror', lambda error: errors.append(str(error)))
        page.goto('http://m3.test/')
        page.wait_for_load_state('networkidle')
        expect(page.get_by_role('heading', name='登录空间服务台')).to_be_visible()
        page.screenshot(path=str(evidence / 'login.png'), full_page=True, animations='disabled')

        def login(username):
            page.get_by_label('账号', exact=True).fill(username)
            page.get_by_label('密码', exact=True).fill(PASSWORD)
            page.get_by_role('button', name='登录', exact=True).click()
            expect(page.locator('.hero h1')).to_be_visible()

        def logout():
            page.get_by_role('button', name='退出 / 切换账号', exact=True).click()
            expect(page.get_by_role('heading', name='登录空间服务台')).to_be_visible()

        def confirm():
            page.get_by_role('button', name='确认提交', exact=True).click()
            expect(page.locator('#modal')).not_to_be_visible()

        login('alice')
        page.get_by_role('button', name='＋ 提交报修').click()
        expect(page.locator('#modal')).to_be_visible()
        page.locator('select[name="room_id"]').select_option('1')
        page.get_by_label('故障描述').fill('灯具损坏，需要检修 <img src=x onerror=alert(1)>')
        confirm()
        expect(page.locator('.ticket-row')).to_have_count(1)
        expect(page.locator('.detail-body')).to_contain_text('<img src=x onerror=alert(1)>')
        expect(page.locator('.detail-body img')).to_have_count(0)
        logout()
        login('admin')
        page.get_by_role('button', name='详情', exact=True).click()
        page.get_by_role('button', name='派单 / 转派').click()
        page.locator('select[name="assignee_id"]').select_option(env.users['tech'])
        confirm()
        tick(env.app)
        page.get_by_role('button', name='刷新', exact=True).click()
        expect(page.locator('.detail-head .tag')).to_have_text('维修中')
        page.screenshot(path=str(evidence / 'workbench.png'), full_page=True, animations='disabled')
        logout()
        login('tech')
        page.get_by_role('button', name='详情', exact=True).click()
        page.get_by_role('button', name='提交完工').click()
        page.get_by_label('维修记录').fill('灯管更换并检查线路，照明正常')
        confirm()
        logout()
        login('admin')
        page.get_by_role('button', name='详情', exact=True).click()
        page.get_by_role('button', name='验收工单', exact=True).click()
        page.get_by_label('验收说明').fill('现场验收照明恢复')
        confirm()
        tick(env.app)
        logout()
        login('alice')
        page.get_by_role('button', name='详情', exact=True).click()
        page.get_by_role('button', name='评价服务').click()
        page.get_by_label('评价（选填）').fill('问题已解决')
        confirm()
        expect(page.locator('.detail-body')).to_contain_text('5 / 5')
        page.get_by_role('button', name='站内通知', exact=True).click()
        expect(page.locator('.message').first).to_be_visible()
        page.get_by_role('button', name='标记已读', exact=True).first.click()
        expect(page.locator('.message').first).to_contain_text('已读')
        page.set_viewport_size({'width':390,'height':844})
        page.screenshot(path=str(evidence / 'mobile-notifications.png'), full_page=True, animations='disabled')
        assert page.evaluate('document.documentElement.scrollWidth <= window.innerWidth')
        page.set_viewport_size({'width':1440,'height':1000})
        logout()
        login('admin')
        page.get_by_role('button', name='用户与权限', exact=True).click()
        page.get_by_role('button', name='添加账号', exact=True).click()
        page.locator('#user-form input[name="username"]').fill('browser-user')
        page.locator('#user-form input[name="display_name"]').fill('浏览器验收用户')
        page.locator('#user-form input[name="password"]').fill(PASSWORD)
        confirm()
        row=page.get_by_role('row').filter(has_text='browser-user')
        expect(row).to_be_visible()
        row.get_by_role('button', name='调整权限').click()
        page.locator('#access-form select[name="active"]').select_option('false')
        confirm()
        expect(row).to_contain_text('停用')
        page.get_by_role('button', name='操作审计', exact=True).click()
        expect(page.get_by_role('cell', name='user_access_changed', exact=True)).to_be_visible()
        assert not errors, errors
        context.close()
        browser.close()

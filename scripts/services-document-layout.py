#!/usr/bin/env python3
"""Run in the screenshot sidecar's Playwright/Chromium environment."""
import asyncio
import json
from pathlib import Path
from playwright.async_api import async_playwright

async def main():
    output = Path('/tmp/document-previews')
    output.mkdir(exist_ok=True)
    records = []
    async with async_playwright() as pw:
        browser = await pw.chromium.launch(headless=True)
        for width in [1440, 1024, 768, 390, 360]:
            page = await browser.new_page(viewport={'width': width, 'height': 1000}, device_scale_factor=1)
            errors = []
            page.on('pageerror', lambda error: errors.append(str(error)))
            await page.goto('file:///tmp/harness-formal-manual.html')
            await page.evaluate('document.fonts.ready')
            await page.wait_for_timeout(250)
            record = await page.evaluate('''() => {
              const side=document.querySelector('.sidebar');
              const visible=Array.from(document.querySelectorAll('.workflow-node')).filter(x=>x.getBoundingClientRect().height>0);
              const trees=Array.from(document.querySelectorAll('.directory-tree')).map(x=>({id:x.id,whiteSpace:getComputedStyle(x).whiteSpace,lines:x.textContent.split('\\n').length,height:x.getBoundingClientRect().height}));
              return {width:innerWidth,documentWidth:document.documentElement.scrollWidth,documentHeight:document.documentElement.scrollHeight,bodyWidth:document.body.scrollWidth,noPageOverflow:document.documentElement.scrollWidth<=innerWidth,sidebarPosition:getComputedStyle(side).position,sidebarOverflow:getComputedStyle(side).overflowY,trees,visibleWorkflowNodes:visible.length,clippedWorkflowNodes:visible.filter(x=>{let r=x.getBoundingClientRect();return r.left< -1 || r.right>innerWidth+1}).map(x=>x.id),activeAtTop:document.querySelector('.sidebar [aria-current]')?.getAttribute('href')};
            }''')
            await page.screenshot(path=str(output/f'formal-{width}-full.png'), full_page=True)
            # Exercise actual scroll event and sticky navigation on desktop.
            await page.evaluate("document.querySelector('#phase-c').scrollIntoView({block:'start',behavior:'instant'})")
            await page.wait_for_timeout(400)
            record['scrollspy'] = await page.evaluate("({active:document.querySelector('.sidebar [aria-current]')?.getAttribute('href'), sidebarTop:document.querySelector('.sidebar').getBoundingClientRect().top, scrollY})")
            await page.screenshot(path=str(output/f'formal-{width}-phase-c.png'))
            record['pageErrors'] = errors
            record['directoryNewlinesPreserved'] = all(t['lines'] > 1 and t['whiteSpace'] in ['pre','pre-wrap','break-spaces'] for t in record['trees'])
            record['sidebarStickyVerified'] = width < 821 or (record['sidebarPosition']=='sticky' and 0<=record['scrollspy']['sidebarTop']<=20 and record['sidebarOverflow']=='auto')
            record['scrollspyVerified'] = record['scrollspy']['active']=='#phase-c'
            record['status'] = 'PASS' if record['noPageOverflow'] and not record['clippedWorkflowNodes'] and record['directoryNewlinesPreserved'] and record['sidebarStickyVerified'] and record['scrollspyVerified'] and not errors else 'FAIL'
            records.append(record)
            await page.close()
        await browser.close()
    result = {'status': 'PASS' if all(x['status']=='PASS' for x in records) else 'FAIL', 'validation_kind':'ACTUAL_CHROMIUM_VIEWPORT_LAYOUT', 'source':'docs/codex_engineering_harness_v3_formal_manual.html', 'static_contract_validation':'Separate primary-agent check; this report verifies live layout only.', 'viewports': records}
    (output/'document-validation.json').write_text(json.dumps(result,ensure_ascii=False,indent=2)+'\n')
    print(json.dumps({'status':result['status'],'viewports':[{k:v for k,v in r.items() if k not in ['trees']} for r in records]},ensure_ascii=False))

asyncio.run(main())

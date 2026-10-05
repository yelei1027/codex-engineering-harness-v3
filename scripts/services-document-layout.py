#!/usr/bin/env python3
"""Verify the generated manual in the owned screenshot sidecar's Chromium."""
import asyncio
import json
from pathlib import Path
from playwright.async_api import async_playwright


async def main():
    output=Path('/tmp/document-previews');output.mkdir(exist_ok=True)
    records=[]
    async with async_playwright() as pw:
        browser=await pw.chromium.launch(headless=True)
        try:
            for width in [1440,1024,768,390,360]:
                page=await browser.new_page(viewport={'width':width,'height':1000},device_scale_factor=1)
                errors=[];page.on('pageerror',lambda error:errors.append(str(error)))
                await page.goto('file:///tmp/harness-formal-manual.html');await page.evaluate('document.fonts.ready')
                await page.locator('#clear').click()
                clear_ok='跳过重复访谈' in await page.locator('#scenario').text_content()
                await page.locator('#vague').click()
                vague_ok='只读' in await page.locator('#scenario').text_content()
                await page.locator('#internal summary').click()
                rows=await page.locator('#internal tbody tr').count()
                await page.locator('#internal summary').click()
                record=await page.evaluate('''() => ({width:innerWidth,documentWidth:document.documentElement.scrollWidth,noPageOverflow:document.documentElement.scrollWidth<=innerWidth,stages:document.querySelectorAll('.flow .step').length,sections:['quick','daily','permissions','architecture','maintenance','recovery','evidence','internal'].every(id=>document.getElementById(id))})''')
                await page.screenshot(path=str(output/f'formal-{width}-full.png'),full_page=True)
                await page.locator('#recovery').screenshot(path=str(output/f'formal-{width}-recovery.png'))
                record.update(contract_rows=rows,scenario_controls=clear_ok and vague_ok,pageErrors=errors)
                record['status']='PASS' if record['noPageOverflow'] and record['stages']==6 and record['sections'] and rows==107 and clear_ok and vague_ok and not errors else 'FAIL'
                records.append(record);await page.close()
        finally:await browser.close()
    result={'status':'PASS' if all(r['status']=='PASS' for r in records) else 'FAIL','validation_kind':'ACTUAL_CHROMIUM_VIEWPORT_LAYOUT','source':'docs/codex_engineering_harness_v3_formal_manual.html','viewports':records}
    (output/'document-validation.json').write_text(json.dumps(result,ensure_ascii=False,indent=2)+'\n')
    print(json.dumps({'status':result['status'],'viewports':records},ensure_ascii=False))


if __name__=='__main__':asyncio.run(main())

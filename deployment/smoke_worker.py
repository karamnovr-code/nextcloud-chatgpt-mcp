"""Synthetic acceptance under the same systemd identity/sandbox as production."""
import asyncio
from io import BytesIO
import json
from pathlib import Path

import pymupdf as fitz
from PIL import Image,ImageDraw,ImageFont

from doc_engine import create_docx,create_xlsx
from jobs import Jobs


async def main():
    root=Path('/var/lib/nextcloud-mcp/acceptance');root.mkdir(exist_ok=True)
    jobs=Jobs(root/'jobs')
    word=create_docx({'title':'Проверка Word','paragraphs':['Синтетический документ для проверки подключения. Контрольная сумма: 15000.']})
    excel=create_xlsx({'sheets':[{'name':'Проверка','rows':[['Наименование','Сумма'],['Материалы',10000],['Работы',5000],['Итого',{'formula':'=SUM(B2:B3)'}]]}]})
    im=Image.new('RGB',(1654,2339),'white'); draw=ImageDraw.Draw(im)
    font=ImageFont.truetype('/usr/share/fonts/truetype/dejavu/DejaVuSans.ttf',44)
    for y,line in enumerate(['ТЕСТОВАЯ ЗАЯВКА','Синтетические данные для проверки OCR','Материалы: 10000 рублей','Работы: 5000 рублей','Итого: 15000 рублей']):
        draw.text((100,160+y*90),line,font=font,fill='black')
    stream=BytesIO();im.save(stream,format='PNG')
    pdf=fitz.open();page=pdf.new_page(width=595,height=842);page.insert_image(page.rect,stream=stream.getvalue());scan=pdf.tobytes();pdf.close()
    (root/'sample.docx').write_bytes(word);(root/'sample.xlsx').write_bytes(excel);(root/'scan.pdf').write_bytes(scan)
    results=[]
    for op,data,name,opts in [('read',scan,'scan.pdf',{'start':1,'count':10}),('convert',word,'sample.docx',{'target_format':'pdf'}),('convert',excel,'sample.xlsx',{'target_format':'pdf'}),('convert',scan,'scan.pdf',{'target_format':'docx'}),('convert',scan,'scan.pdf',{'target_format':'xlsx'})]:
        handle=await jobs.submit(op,data,name,opts,'test-owner')
        for _ in range(600):
            status=await jobs.status(handle['job_id'],'test-owner')
            if status['status']!='running':break
            await asyncio.sleep(.1)
        assert status['status']=='completed',status
        if op=='read':
            assert '15000' in json.dumps(status,ensure_ascii=False),status
        else:
            output,ext=jobs.artifact(handle['job_id'],'test-owner')
            (root/(name+'.'+ext)).write_bytes(output)
        results.append({'operation':op,'source':name,'target':opts.get('target_format'),'status':'passed'})
    (root/'receipt.json').write_text(json.dumps(results,ensure_ascii=False,indent=2))
    print(json.dumps({'status':'passed','checks':results},ensure_ascii=False))


asyncio.run(main())

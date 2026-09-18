import asyncio,json
from pathlib import Path
from io import BytesIO
from docx import Document
from docx.shared import Pt
from jobs import Jobs
async def main():
 root=Path('/var/lib/nextcloud-mcp/acceptance/rules');root.mkdir(exist_ok=True)
 jobs=Jobs(root/'jobs')
 spec={'profile':'official','title':'СПРАВКА','paragraphs':['О проверке оформления документов','Краткий вывод: новый официальный документ сформирован по заданному профилю. Контрольная сумма составляет 15 000 рублей.','Материалы — 10 000 рублей, работы — 5 000 рублей. Расхождений не установлено.'],'tables':[{'rows':[['Показатель','Сумма, руб.'],['Материалы','10 000'],['Работы','5 000'],['Итого','15 000']]}]}
 async def finish(h):
  for _ in range(600):
   s=await jobs.status(h['job_id'],'test-owner')
   if s['status']!='running':break
   await asyncio.sleep(.1)
  assert s['status']=='completed',s
  return jobs.artifact(h['job_id'],'test-owner')[0]
 d=await finish(await jobs.submit('create_docx',b'','official.docx',spec,'test-owner'))
 doc=Document(BytesIO(d));assert doc.styles['Normal'].font.name=='Times New Roman';assert doc.styles['Normal'].font.size==Pt(14)
 (root/'official.docx').write_bytes(d)
 pdf=await finish(await jobs.submit('convert',d,'official.docx',{'target_format':'pdf'},'test-owner'))
 (root/'official.pdf').write_bytes(pdf)
 print(json.dumps({'official_profile':'passed','pdf_bytes':len(pdf)}))
asyncio.run(main())

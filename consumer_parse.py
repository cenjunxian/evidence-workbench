"""Bounded text-only PDF worker. No OCR, links, shell or document actions."""
import io,json,sys,zipfile
from xml.etree import ElementTree as ET
if sys.platform!='win32':
    import resource
    resource.setrlimit(resource.RLIMIT_AS,(192*1024**2,192*1024**2))
    resource.setrlimit(resource.RLIMIT_CPU,(5,5))
from pypdf import PdfReader

def parse_pdf(raw):
    if not raw.startswith(b'%PDF-'):raise ValueError('损坏或非PDF文件。')
    reader=PdfReader(io.BytesIO(raw),strict=True)
    if reader.is_encrypted:raise ValueError('不支持加密PDF。')
    if not 1<=len(reader.pages)<=10:raise ValueError('PDF限1至10页。')
    segments=[];total=0
    for index,page in enumerate(reader.pages,1):
        text=(page.extract_text() or '').replace('\x00','').strip()
        if not text:raise ValueError('第%d页没有可提取文字；扫描页或空白页不支持，本次未导入任何页面。'%index)
        total+=len(text)
        if total>8000:raise ValueError('单文件文字超过8000字符，请拆分。')
        for number,start in enumerate(range(0,len(text),1000),1):
            segments.append({'part':f'p{index}s{number}','text':text[start:start+1000],'page':index,'line_start':None,'line_end':None})
    return segments

W='http://schemas.openxmlformats.org/wordprocessingml/2006/main'
def parse_docx(raw):
    if not raw.startswith(b'PK\x03\x04'):raise ValueError('损坏、加密或非DOCX文件；请用Word另存为未加密.docx。')
    warnings=[]
    def warn(value):
        if value not in warnings:warnings.append(value)
    def xml(data):
        scan=data.replace(b'\x00',b'').upper()
        if b'<!DOCTYPE' in scan or b'<!ENTITY' in scan:raise ValueError('文档含不支持的XML实体，未导入。')
        root=ET.fromstring(data)
        if sum(1 for _ in root.iter())>20000:raise ValueError('文档结构过于复杂，请拆分。')
        return root
    with zipfile.ZipFile(io.BytesIO(raw)) as archive:
        infos=archive.infolist();names=[i.filename for i in infos]
        if len(infos)>256 or len(set(names))!=len(names):raise ValueError('DOCX部件过多或重名，请拆分或重新保存。')
        if any(i.flag_bits&1 for i in infos):raise ValueError('不支持加密DOCX，请另存为未加密文档。')
        if sum(i.file_size for i in infos)>8*1024**2 or any(i.file_size>2*1024**2 or i.file_size/max(i.compress_size,1)>200 for i in infos):raise ValueError('DOCX展开大小或压缩比超限，未导入；请拆分。')
        if any('..' in i.filename.split('/') or i.filename.startswith(('/', '\\')) for i in infos):raise ValueError('DOCX部件路径无效。')
        if not {'[Content_Types].xml','_rels/.rels','word/document.xml'}<=set(names):raise ValueError('缺少DOCX必要部件；请重新另存为.docx。')
        types=xml(archive.read('[Content_Types].xml'))
        if not any(n.get('PartName')=='/word/document.xml' and n.get('ContentType')=='application/vnd.openxmlformats-officedocument.wordprocessingml.document.main+xml' for n in types):raise ValueError('实际格式不是无宏DOCX；不支持改后缀或宏文档。')
        rels=xml(archive.read('_rels/.rels'))
        if not any(n.get('Type','').endswith('/officeDocument') and n.get('Target','').lstrip('/')=='word/document.xml' and n.get('TargetMode')!='External' for n in rels):raise ValueError('DOCX正文关系无效。')
        if any('vbaproject' in name.lower() for name in names):raise ValueError('不支持带宏的文档。')
        root=None
        revisions={'ins','del','delText','delInstrText','moveFrom','moveTo','cellIns','cellDel','cellMerge','numberingChange'}
        for name in names:
            if name.startswith('word/') and name.endswith('.xml'):
                part=xml(archive.read(name))
                for node in part.iter():
                    local=node.tag.split('}')[-1]
                    if node.tag.startswith('{'+W+'}') and (local in revisions or local.endswith('PrChange') or local.startswith(('moveFromRange','moveToRange'))):raise ValueError('文档含未处理修订；请先确认并接受或拒绝修订，再另存上传，避免混用前后正文。')
                if name=='word/document.xml':root=part
        if any(n.startswith('word/media/') for n in names):warn('图片文字未读取，不支持OCR。')
        if any(n.startswith('word/comments') for n in names):warn('批注未读取，不作为有效正文。')
        if any(n.startswith(('word/header','word/footer','word/footnotes','word/endnotes')) for n in names):warn('页眉、页脚、脚注及尾注未读取。')
        if any(n.startswith('word/embeddings/') for n in names):warn('嵌入附件未读取。')
        segments=[];total=0
        def tag(n):return n.tag.split('}')[-1]
        def paragraph_text(n):
            local=tag(n)
            if local in ('drawing','pict','txbxContent','object'):
                warn('图片、文本框及对象文字未读取。');return ''
            if local=='t':return n.text or ''
            if local=='tab':return '\t'
            if local in ('br','cr'):return '\n'
            if local in ('fldChar','instrText'):warn('域仅读取已保存的显示文字，未计算或更新。');return ''
            return ''.join(paragraph_text(c) for c in n)
        def add(text,identity,location):
            nonlocal total
            text=text.replace('\x00','').strip()
            if not text:return
            total+=len(text)
            if total>8000:raise ValueError('单文件文字超过8000字符，请拆分。')
            for start in range(0,len(text),1000):
                segments.append({'part':identity+'s'+str(start//1000+1),'text':text[start:start+1000],'location':location,'page':None,'line_start':None,'line_end':None})
            if len(segments)>80:raise ValueError('最多80个来源片段，请拆分。')
        def blocks(parent,prefix='',label='正文'):
            pn=tn=0
            for node in parent:
                local=tag(node)
                if local=='p':
                    pn+=1;add(paragraph_text(node),prefix+f'p{pn}',label+f'第{pn}段')
                elif local=='tbl':
                    tn+=1
                    rows=[r for r in node if tag(r)=='tr']
                    for ri,row in enumerate(rows,1):
                        for ci,cell in enumerate([c for c in row if tag(c)=='tc'],1):
                            blocks(cell,prefix+f't{tn}r{ri}c{ci}',label+f'表{tn}／第{ri}行第{ci}格／')
                elif local=='sdt':
                    warn('内容控件按已保存文字读取，不执行交互。')
                    for content in node:
                        if tag(content)=='sdtContent':blocks(content,prefix+f'c{list(parent).index(node)}',label+'内容控件／')
                elif local not in ('sectPr','tcPr'):
                    warn('部分非正文结构未读取：'+local[:40]+'。')
        body=root.find('{'+W+'}body') if root is not None else None
        if body is None:raise ValueError('DOCX缺少正文。')
        blocks(body)
        if not segments:raise ValueError('正文及表格没有可读取文字；图片、空文档不支持。')
        return {'segments':segments,'parse_warnings':warnings,'extraction_scope':'只读取主文档正文段落与表格中已保存的文字，按文档结构定位；不提供原排版页码。图片文字、批注、页眉页脚、脚注尾注、文本框和嵌入附件不在读取范围。'}

try:
    raw=sys.stdin.buffer.read(1024**2+1)
    if len(raw)>1024**2:raise ValueError('单文件不能超过1MiB。')
    value={'ok':True,**(parse_docx(raw) if sys.argv[1:]==['docx'] else {'segments':parse_pdf(raw),'parse_warnings':[],'extraction_scope':'文字PDF提取文本，按页定位；不支持OCR。'})}
except ValueError as exc:value={'ok':False,'error':str(exc)[:160]}
except Exception:value={'ok':False,'error':'文档损坏、加密或无法解析；没有导入材料。'}
sys.stdout.buffer.write(json.dumps(value,ensure_ascii=False).encode('utf-8'))

"""Loopback-only HTTP server, static allowlist, JSON writes and same-origin checks."""
import json
from email.parser import BytesParser
from email.policy import default
from http.server import BaseHTTPRequestHandler,ThreadingHTTPServer
from pathlib import Path
from urllib.parse import parse_qs,urlparse,quote

from .demo import settings_for_ui
from .service import UIError,UIService


def make_server(service,port=8501,*,desktop=None):
    class Handler(BaseHTTPRequestHandler):
        def log_message(self,*args):pass  # Questions and source text never enter access logs.
        def send(self,status,data,content_type='application/json; charset=utf-8',filename=None,attachment=False):
            body=json.dumps(data,ensure_ascii=False).encode('utf-8') if content_type.startswith('application/json') else data
            self.send_response(status)
            self.send_header('Content-Type',content_type);self.send_header('Content-Length',str(len(body)))
            self.send_header('Cache-Control','no-store');self.send_header('X-Content-Type-Options','nosniff')
            if filename:self.send_header('Content-Disposition',('attachment' if attachment else 'inline')+"; filename*=UTF-8''"+quote(filename,safe=''))
            self.send_header('Content-Security-Policy',"default-src 'self'; script-src 'self'; style-src 'self'; connect-src 'self'; img-src 'self' data:; frame-ancestors 'none'; base-uri 'none'; form-action 'self'")
            self.end_headers();self.wfile.write(body)
        def dispatch(self):
            port=self.server.server_port
            hosts={f'127.0.0.1:{port}',f'localhost:{port}'}
            if self.headers.get('Host') not in hosts:raise UIError('只接受本机访问。',403)
            origin=self.headers.get('Origin')
            if origin and origin not in {f'http://{host}' for host in hosts}:raise UIError('不接受其他站点发起的操作。',403)
            parsed=urlparse(self.path);query=parse_qs(parsed.query)
            if self.command=='GET':
                if parsed.path=='/api/desktop' and desktop is not None:
                    self.send(200,desktop);return
                assets={'/':('index.html','text/html; charset=utf-8'),'/app.js':('app.js','text/javascript; charset=utf-8'),'/models.js':('models.js','text/javascript; charset=utf-8'),'/question-scope.js':('question_scope.js','text/javascript; charset=utf-8'),'/courses.js':('course_prepare.js','text/javascript; charset=utf-8'),'/style.css':('style.css','text/css; charset=utf-8')}
                assets['/question-text.js']=('question_text.js','text/javascript; charset=utf-8')
                assets['/setup.js']=('setup.js','text/javascript; charset=utf-8')
                if parsed.path in assets:
                    name,mime=assets[parsed.path];self.send(200,(Path(__file__).parent/'static'/name).read_bytes(),mime);return
                if parsed.path=='/api/job':result=service.job(query.get('id',[''])[0])
                elif parsed.path=='/api/question-image':
                    body,mime=service.call('_question_image',int(query.get('id',['0'])[0]),int(query.get('index',['0'])[0]))
                    self.send(200,body,mime);return
                elif parsed.path=='/api/question-page':
                    body,mime=service.call('_question_page',int(query.get('id',['0'])[0]),int(query.get('page',['0'])[0]))
                    self.send(200,body,mime);return
                elif parsed.path=='/api/question-source':
                    body,mime,name=service.call('_question_source',int(query.get('id',['0'])[0]),int(query.get('source',['0'])[0]))
                    self.send(200,body,mime,filename=name);return
                elif parsed.path=='/api/course-source':
                    body,mime,name=service.call('_course_source',query.get('course',[''])[0],int(query.get('source',['0'])[0]))
                    self.send(200,body,mime,filename=name,attachment=mime!='application/pdf');return
                elif parsed.path=='/api/bootstrap':result=service.call('_bootstrap')
                elif parsed.path=='/api/model-settings':result=service.call('_model_settings')
                elif parsed.path=='/api/local-model-settings':result=service.call('_local_model_settings')
                elif parsed.path=='/api/history':result=service.call('_history',query.get('session',[''])[0])
                elif parsed.path=='/api/maintenance':result=service.call('_maintenance',query.get('session',[''])[0])
                elif parsed.path=='/api/course-status':result=service.call('_course_status',query.get('course',[None])[0])
                elif parsed.path=='/api/health':result={'status':'ok'}
                else:raise UIError('页面或接口不存在。',404)
                self.send(200,result);return
            if self.command!='POST':raise UIError('不支持此请求方法。',405)
            if parsed.path=='/api/course-upload':
                from ..question_bank.supplements import MAX_UPLOAD_BYTES
                content_type=self.headers.get('Content-Type','')
                if content_type.split(';')[0]!='multipart/form-data':raise UIError('上传须使用文件表单。',415)
                message=BytesParser(policy=default).parsebytes(('Content-Type: '+content_type+'\r\nMIME-Version: 1.0\r\n\r\n').encode('ascii')+self.request_body)
                if not message.is_multipart() or message.defects:raise UIError('文件表单不完整，请重新上传。')
                fields={};files=[]
                for part in message.iter_parts():
                    name=part.get_param('name',header='content-disposition')
                    if part.get_content_disposition()!='form-data' or part.is_multipart():raise UIError('文件表单格式不正确。')
                    body=part.get_payload(decode=True)
                    if name=='files' and part.get_filename() is not None:
                        files.append({'name':part.get_filename(),'content':body})
                    elif name in ('course','session','replace') and name not in fields and len(body or b'')<=4096:
                        fields[name]=(body or b'').decode('utf-8')
                    else:raise UIError('文件表单字段无效或重复。')
                    if len(files)>20:raise UIError('每次最多上传 20 个文件。')
                if fields.get('replace','false') not in ('true','false'):raise UIError('替换资料选项无效。')
                fields['replace']=fields.get('replace')=='true'
                if sum(len(item['content'] or b'') for item in files)>MAX_UPLOAD_BYTES:raise UIError('本批文件合计不能超过 100 MiB。',413)
                self.send(200,service.call('_supplement_course',fields,files));return
            if self.headers.get('Content-Type','').split(';')[0]!='application/json':raise UIError('操作须发送 JSON。',415)
            try:payload=json.loads(self.request_body.decode('utf-8'))
            except (UnicodeDecodeError,json.JSONDecodeError):raise UIError('JSON 格式不正确。')
            if not isinstance(payload,dict):raise UIError('请求须为 JSON 对象。')
            routes={'/api/chat':('_chat',payload),'/api/resume':('_resume',payload.get('session','')),
                '/api/correct':('_correct',payload),'/api/import':('_import',payload),
                '/api/delete-document':('_delete_document',payload.get('id',0)),'/api/refresh':('_refresh',),
                '/api/mode':('_mode',payload.get('mode')),
                '/api/model-settings':('_save_model_settings',payload),
                '/api/model-test':('_test_model_settings',payload),
                '/api/model-catalog':('_model_catalog',payload),
                '/api/local-model-settings':('_save_local_model',payload),
                '/api/local-model-test':('_local_model_operation',payload,'test'),
                '/api/local-model-catalog':('_local_model_operation',payload,'models'),
                '/api/mineru-sample':('_mineru_sample',payload),
                '/api/course-catalog':('_discover_courses',payload)}
            if parsed.path=='/api/desktop-stop' and desktop is not None:
                import threading
                self.send(200,{'stopping':True})
                threading.Thread(target=self.server.shutdown,daemon=True).start();return
            if parsed.path in routes:result=service.start_job(*routes[parsed.path])
            elif parsed.path=='/api/setup-complete':result=service.call('_setup_complete')
            elif parsed.path=='/api/session':result=service.call('_create_session',payload)
            elif parsed.path=='/api/question-scope':result=service.call('_question_scope',payload)
            elif parsed.path=='/api/feedback':result=service.call('_feedback',payload)
            elif parsed.path=='/api/skill':result=service.call('_skill',payload)
            elif parsed.path=='/api/prepare-course':result=service.call('_prepare_course',payload)
            elif parsed.path=='/api/course-urls':result=service.call('_supplement_course',payload)
            elif parsed.path=='/api/course-retry':result=service.call('_retry_course_source',payload)
            else:raise UIError('接口不存在。',404)
            self.send(200,result)
        def handle_request(self):
            try:
                self.request_body=b''
                if self.command=='POST':
                    try:length=int(self.headers.get('Content-Length','0'))
                    except ValueError:raise UIError('请求长度不正确。')
                    from ..question_bank.supplements import MAX_UPLOAD_BYTES
                    maximum=MAX_UPLOAD_BYTES+1024*1024 if urlparse(self.path).path=='/api/course-upload' else 2000000
                    if not 0<length<=maximum:raise UIError('请求为空或超过大小限制。',413)
                    # Consume bounded bodies before rejecting an origin/type.
                    # Closing with unread bytes can reset the connection on Windows,
                    # hiding the intended 403/415 response from the client.
                    self.request_body=self.rfile.read(length)
                    if len(self.request_body)!=length:raise UIError('上传不完整，请重新提交。')
                self.dispatch()
            except UIError as exc:self.send(exc.status,{'error':str(exc)})
            except (ValueError,KeyError,TypeError):self.send(400,{'error':'输入格式不正确，请检查字段。'})
            except Exception as exc:self.send(500,{'error':f'本地操作失败（{type(exc).__name__}），请检查配置。'})
        do_GET=handle_request
        do_POST=handle_request
        do_OPTIONS=handle_request
    server=ThreadingHTTPServer(('127.0.0.1',port),Handler)
    server.daemon_threads=True
    return server


def serve(installed,*,port=8501,directory=Path('data/runtime/ui-synthetic'),data='synthetic',mode='demo',scope=''):
    settings=settings_for_ui(installed,directory,data=data,mode=mode,scope=scope)
    service=UIService(settings,Path(directory)/'ui.sqlite3',data=data)
    server=make_server(service,port)
    print(f'AnotherMe 本地界面：http://127.0.0.1:{server.server_port}（{data}，{settings.mode}）',flush=True)
    try:server.serve_forever(poll_interval=.2)
    except KeyboardInterrupt:pass
    finally:server.server_close();service.close()

import pathlib
import sys
import tempfile
import unittest
import urllib.error
import urllib.request
import json
import threading
sys.path.insert(0,str(pathlib.Path(__file__).resolve().parents[1]))
from agentcheckout import (Guard,Database,Assessor,PayPalSandbox,cents_of,AppHandler,ThreadingHTTPServer,HttpTransport,reviewer_secret)

class FakeTransport:
    def __init__(self,host='https://www.sandbox.paypal.com/checkoutnow?token=ORDER123456'):
        self.urls=[]
        self.host=host
    def post(self,url,payload=None,headers=None,form=False):
        self.urls.append((url,payload,headers,form))
        if url.endswith('oauth2/token'):return {'access_token':'fake-token'}
        if url.endswith('checkout/orders'):
            self.unit=payload['purchase_units'][0]
            return {'id':'ORDER123456','links':[{'rel':'approve','href':self.host}]}
        if url.endswith('capture'):
            if headers.get('Prefer')!='return=representation':return {'id':'ORDER123456','status':'COMPLETED'}
            return {'id':'ORDER123456','status':'COMPLETED','purchase_units':[{'reference_id':self.unit['reference_id'],'payments':{'captures':[{'id':'CAPTURE123','status':'COMPLETED','amount':self.unit['amount']}]}}]}
        if url.endswith('generate'):return {'response':'Verify vendor identity and license.'}
        raise AssertionError('unexpected URL '+url)

class GuardTests(unittest.TestCase):
    def setUp(self):
        self.tmp=tempfile.TemporaryDirectory()
        self.db=Database(pathlib.Path(self.tmp.name)/'test.sqlite3')
        self.transport=FakeTransport()
        self.guard=Guard(self.db,PayPalSandbox(self.transport,'sandbox-client','sandbox-secret'),Assessor())
    def tearDown(self):
        self.db.db.close();self.tmp.cleanup()
    def _request(self):return self.guard.request('Dataset','Need research data', '18.50')
    def test_amounts(self):
        for i in ('0','-2','NaN','Infinity','0.0001','500.01'):
            with self.subTest(i=i):
                with self.assertRaises(ValueError):cents_of(i)
        self.assertEqual(cents_of('0.01'),1)
        self.assertEqual(cents_of('500.00'),50000)
    def test_request_is_pending(self):
        r=self._request();self.assertEqual(self.db.get(r['id'])['status'],'PENDING_REVIEW')
    def test_no_checkout_before_approval(self):
        r=self._request()
        with self.assertRaises(ValueError):self.guard.checkout(r['id'])
        self.assertEqual(self.transport.urls,[])
    def test_rejection_is_terminal(self):
        r=self._request();self.guard.decide(r['id'],False)
        with self.assertRaises(ValueError):self.guard.checkout(r['id'])
        with self.assertRaises(ValueError):self.guard.decide(r['id'],True)
    def test_boolean_required(self):
        r=self._request()
        with self.assertRaises(ValueError):self.guard.decide(r['id'],'true')
    def test_checkout_requires_credentials(self):
        g=Guard(self.db,PayPalSandbox(self.transport,'',''))
        r=g.request('Dataset','test','1.00');g.decide(r['id'],True)
        with self.assertRaises(RuntimeError):g.checkout(r['id'])
        self.assertEqual(self.db.get(r['id'])['status'],'APPROVED')
    def test_sandbox_order_correct(self):
        r=self._request();self.guard.decide(r['id'],True);s=self.guard.checkout(r['id'])
        self.assertEqual(s['status'],'AWAITING_BUYER')
        self.assertEqual(self.transport.urls[0][0],'https://api-m.sandbox.paypal.com/v1/oauth2/token')
        self.assertEqual(self.transport.urls[1][1]['purchase_units'][0]['amount']['value'],'18.50')
        self.assertEqual(self.transport.urls[1][1]['intent'],'CAPTURE')
        self.assertEqual(self.transport.urls[1][2]['Prefer'],'return=representation')
    def test_no_live_approval_link(self):
        self.transport.host='https://www.paypal.com/checkoutnow?token=FAKE'
        r=self._request();self.guard.decide(r['id'],True)
        with self.assertRaises(RuntimeError):self.guard.checkout(r['id'])
        self.assertEqual(self.db.get(r['id'])['status'],'APPROVED')
    def test_capture_requires_order(self):
        r=self._request();self.guard.decide(r['id'],True)
        with self.assertRaises(ValueError):self.guard.capture(r['id'])
    def test_capture_transitions_once(self):
        r=self._request();self.guard.decide(r['id'],True);self.guard.checkout(r['id'])
        cap=self.guard.capture(r['id']);self.assertEqual(cap['capture_id'],'CAPTURE123')
        self.assertEqual(cap['status'],'SANDBOX_CAPTURED')
        with self.assertRaises(ValueError):self.guard.capture(r['id'])
        self.assertEqual(len([u for u in self.transport.urls if u[0].endswith('capture')]),1)
    def test_cannot_double_checkout(self):
        r=self._request();self.guard.decide(r['id'],True);self.guard.checkout(r['id'])
        with self.assertRaises(ValueError):self.guard.checkout(r['id'])
    def test_risk_no_ai_label(self):
        self.assertIn('rules-only',self.guard.request('Hardware','low budget',10)['assessment_mode'])
    def test_local_ollama_advisory(self):
        a=Assessor(self.transport,'qwen-test')
        rating,note,mode=a.review('Dataset','test',1000)
        self.assertEqual(rating,'LOW');self.assertIn('Ollama',mode)
        self.assertIn('Verify vendor',note)
    def test_validation_of_text(self):
        with self.assertRaises(ValueError):self.guard.request('', 'reason',1)
        with self.assertRaises(ValueError):self.guard.request('a','',1)
    def test_http_endpoints_and_csrf(self):
        AppHandler.guard=self.guard;AppHandler.token='fixed-test-token';AppHandler.reviewer_token='private-reviewer-test-token'
        server=ThreadingHTTPServer(('127.0.0.1',0),AppHandler)
        t=threading.Thread(target=server.serve_forever,daemon=True);t.start()
        try:
            url='http://127.0.0.1:'+str(server.server_port)
            with urllib.request.urlopen(url+'/api/state') as resp:
                self.assertEqual(json.load(resp)['csrf'],'fixed-test-token')
            req=urllib.request.Request(url+'/api/request',method='POST',data=json.dumps({'item':'Laptop','justification':'QA','amount':'49.00'}).encode(),headers={'Content-Type':'application/json','X-CSRF-Token':'fixed-test-token'})
            with urllib.request.urlopen(req) as resp:self.assertEqual(json.load(resp)['status'],'PENDING_REVIEW')
            req=urllib.request.Request(url+'/api/request',method='POST',data=b'{}',headers={'Content-Type':'application/json','X-CSRF-Token':'wrong'})
            with self.assertRaises(urllib.error.HTTPError) as e:urllib.request.urlopen(req)
            self.assertEqual(e.exception.code,403)
        finally:server.shutdown();server.server_close();t.join(timeout=2)

    def test_exact_payer_url_and_token(self):
        bad_links=(
            'https://www.sandbox.paypal.com/checkoutnow?token=OTHER12345',
            'https://www.sandbox.paypal.com.evil.invalid/checkoutnow?token=ORDER123456',
            'https://evil@www.sandbox.paypal.com/checkoutnow?token=ORDER123456',
            'https://www.sandbox.paypal.com/anything?token=ORDER123456',
            'https://www.sandbox.paypal.com/checkoutnow?token=ORDER123456&token=ORDER123456',
            'https://www.sandbox.paypal.com/checkoutnow?token=ORDER123456#fragment',
            'https://www.sandbox.paypal.com/checkoutnow?token=ORDER123456&redirect=evil',
            'http://www.sandbox.paypal.com/checkoutnow?token=ORDER123456',
        )
        r=self._request();self.guard.decide(r['id'],True)
        for link in bad_links:
            with self.subTest(link=link):
                self.transport.host=link
                with self.assertRaises(RuntimeError):self.guard.checkout(r['id'])
                self.assertEqual(self.db.get(r['id'])['status'],'APPROVED')

    def test_capture_validates_identity_amount_currency_and_cardinality(self):
        r=self._request();self.guard.decide(r['id'],True);self.guard.checkout(r['id'])
        original=self.transport.post
        def alter(change):
            def post(url,*args,**kwargs):
                result=original(url,*args,**kwargs)
                if url.endswith('capture'):change(result)
                return result
            return post
        mutations=(
            lambda x:x.update(id='OTHERORDER'),
            lambda x:x['purchase_units'][0].update(reference_id='other-request'),
            lambda x:x['purchase_units'][0]['payments']['captures'][0].update(amount={'currency_code':'USD','value':'18.49'}),
            lambda x:x['purchase_units'][0]['payments']['captures'][0].update(amount={'currency_code':'EUR','value':'18.50'}),
            lambda x:x['purchase_units'][0]['payments']['captures'][0].update(status='PENDING'),
            lambda x:x['purchase_units'].append(x['purchase_units'][0]),
            lambda x:x['purchase_units'][0]['payments']['captures'].append({'status':'COMPLETED','id':'CAPTUREOTHER'}),
        )
        for mutate in mutations:
            with self.subTest(mutation=mutate):
                self.transport.post=alter(mutate)
                with self.assertRaises(RuntimeError):self.guard.capture(r['id'])
                self.assertEqual(self.db.get(r['id'])['status'],'AWAITING_BUYER')
        self.transport.post=original

    def _concurrent(self,operation):
        entered=threading.Event();release=threading.Event();original=self.transport.post
        errors=[];success=[]
        def blocked(url,*args,**kwargs):
            if url.endswith('checkout/orders') or url.endswith('capture'):
                entered.set()
                if not release.wait(2):raise AssertionError('test timed out')
            return original(url,*args,**kwargs)
        self.transport.post=blocked
        def work():
            try:success.append(operation())
            except ValueError as exc:errors.append(exc)
        first=threading.Thread(target=work);second=threading.Thread(target=work)
        first.start();self.assertTrue(entered.wait(2));second.start();release.set()
        first.join(2);second.join(2)
        self.assertFalse(first.is_alive());self.assertFalse(second.is_alive())
        self.assertEqual(len(success),1);self.assertEqual(len(errors),1)
    def test_concurrent_checkout_has_one_external_operation(self):
        r=self._request();self.guard.decide(r['id'],True)
        self._concurrent(lambda:self.guard.checkout(r['id']))
        self.assertEqual(sum(url.endswith('checkout/orders') for url,*_ in self.transport.urls),1)
    def test_concurrent_capture_has_one_external_operation(self):
        r=self._request();self.guard.decide(r['id'],True);self.guard.checkout(r['id'])
        self._concurrent(lambda:self.guard.capture(r['id']))
        self.assertEqual(sum(url.endswith('capture') for url,*_ in self.transport.urls),1)

    def test_ambiguous_create_retry_key_survives_restart(self):
        r=self._request();self.guard.decide(r['id'],True)
        original=self.transport.post
        def ambiguous(url,*args,**kwargs):
            result=original(url,*args,**kwargs)
            if url.endswith('checkout/orders'):raise TimeoutError('response lost')
            return result
        self.transport.post=ambiguous
        with self.assertRaises(TimeoutError):self.guard.checkout(r['id'])
        key=self.transport.urls[-1][2]['PayPal-Request-Id']
        self.db.db.close();self.db=Database(pathlib.Path(self.tmp.name)/'test.sqlite3')
        self.transport.post=original
        self.guard=Guard(self.db,PayPalSandbox(self.transport,'sandbox-client','sandbox-secret'),Assessor())
        self.guard.checkout(r['id'])
        self.assertEqual(self.transport.urls[-1][2]['PayPal-Request-Id'],key)

    def test_ambiguous_capture_retries_same_key(self):
        r=self._request();self.guard.decide(r['id'],True);self.guard.checkout(r['id'])
        original=self.transport.post
        def ambiguous(url,*args,**kwargs):
            result=original(url,*args,**kwargs)
            if url.endswith('capture'):raise TimeoutError('response lost')
            return result
        self.transport.post=ambiguous
        with self.assertRaises(TimeoutError):self.guard.capture(r['id'])
        key=self.transport.urls[-1][2]['PayPal-Request-Id']
        self.db.db.close();self.db=Database(pathlib.Path(self.tmp.name)/'test.sqlite3')
        self.transport.post=original
        self.guard=Guard(self.db,PayPalSandbox(self.transport,'sandbox-client','sandbox-secret'),Assessor())
        self.guard.capture(r['id'])
        self.assertEqual(self.transport.urls[-1][2]['PayPal-Request-Id'],key)
        self.assertEqual(self.transport.urls[-1][2]['Prefer'],'return=representation')

    def test_malformed_external_shapes_fail_closed(self):
        r=self._request();self.guard.decide(r['id'],True)
        original=self.transport.post
        for malformed in ([],{'id':'ORDER123456','links':[None]},{'id':'ORDER123456','links':None}):
            def post(url,*args,**kwargs):
                if url.endswith('checkout/orders'):return malformed
                return original(url,*args,**kwargs)
            self.transport.post=post
            with self.assertRaises(RuntimeError):self.guard.checkout(r['id'])
            self.assertEqual(self.db.get(r['id'])['status'],'APPROVED')
        self.transport.post=original;self.guard.checkout(r['id'])
        for malformed in ([],{'id':'ORDER123456','status':'COMPLETED','purchase_units':[None]},
                          {'id':'ORDER123456','status':'COMPLETED','purchase_units':[{'reference_id':r['id'],'payments':None}]},
                          {'id':'ORDER123456','status':'COMPLETED','purchase_units':[{'reference_id':r['id'],'payments':{'captures':[None]}}]}):
            def post(url,*args,**kwargs):
                if url.endswith('capture'):return malformed
                return original(url,*args,**kwargs)
            self.transport.post=post
            with self.assertRaises(RuntimeError):self.guard.capture(r['id'])
            self.assertEqual(self.db.get(r['id'])['status'],'AWAITING_BUYER')
        self.transport.post=original

    def test_transport_response_size_and_json_type(self):
        from http.server import BaseHTTPRequestHandler
        class ResponseHandler(BaseHTTPRequestHandler):
            def do_POST(self):
                self.send_response(200);self.end_headers()
                self.wfile.write(b'[]' if self.path=='/list' else b' '* (1024*1024+1))
            def log_message(self,*args):pass
        server=ThreadingHTTPServer(('127.0.0.1',0),ResponseHandler)
        thread=threading.Thread(target=server.serve_forever,daemon=True);thread.start()
        try:
            for path in ('/list','/large'):
                with self.assertRaises(RuntimeError):HttpTransport().post('http://127.0.0.1:'+str(server.server_port)+path)
        finally:server.shutdown();server.server_close();thread.join(2)

    def test_expired_retry_requires_reconciliation(self):
        r=self._request();self.guard.decide(r['id'],True)
        self.db.operation_key(r['id'],'checkout')
        self.db.db.execute('UPDATE operations SET started=0');self.db.db.commit()
        with self.assertRaises(RuntimeError):self.guard.checkout(r['id'])
        self.assertEqual(self.transport.urls,[])

    def test_reviewer_auth_and_host_defense(self):
        AppHandler.guard=self.guard;AppHandler.token='csrf-test';AppHandler.reviewer_token='reviewer-private'
        server=ThreadingHTTPServer(('127.0.0.1',0),AppHandler)
        thread=threading.Thread(target=server.serve_forever,daemon=True);thread.start()
        url='http://127.0.0.1:'+str(server.server_port)
        try:
            with urllib.request.urlopen(url+'/api/state') as response:
                state=json.load(response)
                self.assertNotIn('reviewer-private',json.dumps(state))
            r=self._request()
            for path,data in (('/api/decide',{'id':r['id'],'approve':True}),('/api/checkout',{'id':r['id']}),('/api/capture',{'id':r['id']})):
                req=urllib.request.Request(url+path,data=json.dumps(data).encode(),headers={'X-CSRF-Token':'csrf-test'})
                with self.assertRaises(urllib.error.HTTPError) as error:urllib.request.urlopen(req)
                self.assertEqual(error.exception.code,403)
            for method in ('GET','POST'):
                req=urllib.request.Request(url+'/api/state',method=method,data=b'{}' if method=='POST' else None,headers={'Host':'attacker.invalid','X-CSRF-Token':'csrf-test'})
                with self.assertRaises(urllib.error.HTTPError) as error:urllib.request.urlopen(req)
                self.assertEqual(error.exception.code,403)
            req=urllib.request.Request(url+'/api/decide',data=json.dumps({'id':r['id'],'approve':True}).encode(),headers={'X-CSRF-Token':'csrf-test','X-Reviewer-Token':'reviewer-private'})
            with urllib.request.urlopen(req) as response:self.assertEqual(json.load(response)['status'],'APPROVED')
        finally:server.shutdown();server.server_close();thread.join(2)

    def test_transport_does_not_follow_redirect(self):
        from http.server import BaseHTTPRequestHandler
        visits=[]
        class RedirectHandler(BaseHTTPRequestHandler):
            def do_POST(self):
                visits.append(self.path);self.send_response(307);self.send_header('Location','/leak');self.end_headers()
            def do_GET(self):visits.append(self.path);self.send_response(200);self.end_headers()
            def log_message(self,*args):pass
        server=ThreadingHTTPServer(('127.0.0.1',0),RedirectHandler)
        thread=threading.Thread(target=server.serve_forever,daemon=True);thread.start()
        try:
            with self.assertRaises((RuntimeError,urllib.error.HTTPError)):
                HttpTransport().post('http://127.0.0.1:'+str(server.server_port)+'/redirect',{}, {'Authorization':'synthetic-test'})
            self.assertEqual(visits,['/redirect'])
        finally:server.shutdown();server.server_close();thread.join(2)

    def test_reviewer_file_private_and_persistent(self):
        path=pathlib.Path(self.tmp.name)/'reviewer.key'
        token=reviewer_secret(path)
        self.assertGreaterEqual(len(token),32);self.assertEqual(reviewer_secret(path),token)
        self.assertEqual(path.stat().st_mode & 0o077,0)
        path.chmod(0o644)
        with self.assertRaises(ValueError):reviewer_secret(path)

if __name__=='__main__':unittest.main()

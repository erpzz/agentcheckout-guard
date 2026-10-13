"""AgentCheckout Guard: sandbox-only agent-initiated purchase requests."""
from __future__ import annotations
import base64
import datetime as dt
import decimal
import json
import os
import re
import secrets
import sqlite3
import threading
import time
import urllib.parse
import urllib.request
import uuid
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path

DECIMAL=decimal.Decimal
MAX_CENTS=50000
ALLOWED_HOST="https://api-m.sandbox.paypal.com"
MODEL_HOST="http://127.0.0.1:11434"
RETRY_WINDOW_SECONDS=5*60*60  # Below PayPal's default six-hour request-id retention.
MCP_PROTOCOL_VERSIONS={"2025-11-25","2025-06-18","2025-03-26","2024-11-05"}

def approval_url(link,order_id):
    if not isinstance(link,str) or any(ord(c)<33 for c in link):
        raise RuntimeError("Invalid sandbox payer URL")
    parsed=urllib.parse.urlsplit(link)
    if (parsed.scheme!="https" or parsed.netloc not in ("www.sandbox.paypal.com","sandbox.paypal.com")
            or parsed.path!="/checkoutnow" or parsed.fragment
            or urllib.parse.parse_qs(parsed.query,keep_blank_values=True)!={"token":[order_id]}):
        raise RuntimeError("PayPal returned no matching sandbox-only payer approval URL")
    return link

def cents_of(value):
    try:
        num=DECIMAL(str(value))
        if not num.is_finite() or num<=0 or num>DECIMAL(MAX_CENTS)/100 or num.as_tuple().exponent < -2: raise ValueError("invalid amount")
        return int(num*100)
    except (decimal.InvalidOperation,TypeError,ValueError) as exc:
        raise ValueError("Amount must be between $0.01 and $500.00, at most two decimals") from exc

def money(cents): return f"{cents/100:.2f}"
def utcnow():return dt.datetime.now(dt.timezone.utc).isoformat()
def normalized_text(value,field,max_len=200):
    if not isinstance(value,str):raise ValueError(f"{field} must be text")
    value=value.strip()
    if not value or len(value)>max_len:raise ValueError(f"{field} needs 1–{max_len} characters")
    return value

class Database:
    def __init__(self,path):
        self.db=sqlite3.connect(str(path),check_same_thread=False)
        self.db.row_factory=sqlite3.Row
        self.lock=threading.Lock()
        self.operation_lock=threading.RLock()
        with self.lock:
            self.db.executescript("""
            CREATE TABLE IF NOT EXISTS requests (
                id TEXT PRIMARY KEY, created TEXT NOT NULL, item TEXT NOT NULL,
                justification TEXT NOT NULL, cents INTEGER NOT NULL, source TEXT NOT NULL,
                risk TEXT NOT NULL, risk_note TEXT NOT NULL, assessment_mode TEXT NOT NULL,
                status TEXT NOT NULL, paypal_order_id TEXT UNIQUE, checkout_url TEXT,
                capture_id TEXT, updated TEXT NOT NULL);
            CREATE TABLE IF NOT EXISTS operations (
                request_id TEXT NOT NULL, operation TEXT NOT NULL, request_key TEXT NOT NULL,
                started REAL NOT NULL, PRIMARY KEY(request_id,operation));
            """)
            self.db.commit()
    def put(self,d):
        with self.lock:
            self.db.execute("INSERT INTO requests VALUES (?,?,?,?,?,?,?,?,?,?,?,?,?,?)",(
                d["id"],d["created"],d["item"],d["justification"],d["cents"],d["source"],
                d["risk"],d["risk_note"],d["assessment_mode"],d["status"],None,None,None,d["created"]))
            self.db.commit()
        return d
    def get(self,request_id):
        with self.lock:
            row=self.db.execute("SELECT * FROM requests WHERE id=?",(request_id,)).fetchone()
            return dict(row) if row else None
    def all(self):
        with self.lock:
            return [dict(r) for r in self.db.execute("SELECT * FROM requests ORDER BY created DESC LIMIT 100")]
    def transition(self,request_id,expected_status,new_status,**updates):
        allowed={"paypal_order_id","checkout_url","capture_id"}
        if any(k not in allowed for k in updates):raise ValueError("invalid transition data")
        cols=["status=?","updated=?"]+[k+"=?" for k in updates]
        args=[new_status,utcnow()]+list(updates.values())+[request_id,expected_status]
        with self.lock:
            cursor=self.db.execute(f'UPDATE requests SET {", ".join(cols)} WHERE id=? AND status=?',args)
            self.db.commit()
            if cursor.rowcount!=1:raise ValueError(f"Illegal state transition; expected {expected_status}")
        return self.get(request_id)
    def operation_key(self,request_id,operation):
        with self.lock:
            key=request_id if operation=="checkout" else request_id+"-"+operation
            self.db.execute("INSERT OR IGNORE INTO operations VALUES (?,?,?,?)",
                            (request_id,operation,key,time.time()))
            self.db.commit()
            row=self.db.execute("SELECT request_key,started FROM operations WHERE request_id=? AND operation=?",
                                (request_id,operation)).fetchone()
            if time.time()-row["started"]>RETRY_WINDOW_SECONDS:
                raise RuntimeError("Retry window expired; reconcile the sandbox order before retrying")
            return row["request_key"]

class NoRedirect(urllib.request.HTTPRedirectHandler):
    def redirect_request(self,req,fp,code,msg,headers,newurl):
        raise RuntimeError("Outbound redirects are forbidden")

class HttpTransport:
    def post(self,url,payload=None,headers=None,form=False):
        data=urllib.parse.urlencode(payload or {}).encode() if form else json.dumps(payload or {}).encode()
        opener=urllib.request.build_opener(NoRedirect())
        with opener.open(urllib.request.Request(url,data=data,headers=headers or {},method="POST"),timeout=12) as r:
            if r.status<200 or r.status>=300:raise ValueError("API returned unexpected status")
            data=r.read(1024*1024+1)
            if len(data)>1024*1024:raise RuntimeError("API response exceeds local limit")
            result=json.loads(data)
            if not isinstance(result,dict):raise RuntimeError("API response must be a JSON object")
            return result

class PayPalSandbox:
    def __init__(self,transport=None,client_id=None,secret=None):
        self.http=transport or HttpTransport()
        self.client_id=client_id if client_id is not None else os.getenv("PAYPAL_SANDBOX_CLIENT_ID","")
        self.secret=secret if secret is not None else os.getenv("PAYPAL_SANDBOX_CLIENT_SECRET","")
    def ready(self):return bool(self.client_id and self.secret)
    def token(self):
        if not self.ready():raise RuntimeError("Sandbox credentials are not configured")
        basic=base64.b64encode(f"{self.client_id}:{self.secret}".encode()).decode()
        r=self.http.post(ALLOWED_HOST+"/v1/oauth2/token",{"grant_type":"client_credentials"},
            {"Authorization":"Basic "+basic,"Content-Type":"application/x-www-form-urlencoded"},form=True)
        if not isinstance(r,dict):raise RuntimeError("Invalid sandbox authentication response")
        value=r.get("access_token")
        if not isinstance(value,str) or not value:raise RuntimeError("Sandbox authentication failed")
        return value
    def create(self,request,idempotency_key=None):
        token=self.token()
        order=self.http.post(ALLOWED_HOST+"/v2/checkout/orders",{
            "intent":"CAPTURE",
            "purchase_units":[{"reference_id":request["id"],"description":request["item"][:127],
                 "amount":{"currency_code":"USD","value":money(request["cents"])}}],
            "application_context":{"shipping_preference":"NO_SHIPPING","user_action":"PAY_NOW"}
        },{"Authorization":"Bearer "+token,"Content-Type":"application/json","PayPal-Request-Id":idempotency_key or request["id"],"Prefer":"return=representation"})
        if not isinstance(order,dict):raise RuntimeError("Invalid sandbox order response")
        order_id=order.get("id")
        if not isinstance(order_id,str) or not re.fullmatch(r"[A-Za-z0-9_-]{6,64}",order_id):
            raise RuntimeError("PayPal returned invalid order id")
        links=order.get("links",[])
        if not isinstance(links,list) or any(not isinstance(x,dict) for x in links):
            raise RuntimeError("Invalid sandbox order links")
        link=next((x.get("href") for x in links if x.get("rel") in ("approve","payer-action")),None)
        return order_id,approval_url(link,order_id)
    def capture(self,request,idempotency_key):
        order_id=request["paypal_order_id"]
        if not isinstance(order_id,str) or not re.fullmatch(r"[A-Za-z0-9_-]{6,64}",order_id):
            raise ValueError("invalid order id")
        token=self.token()
        response=self.http.post(ALLOWED_HOST+"/v2/checkout/orders/"+order_id+"/capture",{},{
            "Authorization":"Bearer "+token,"Content-Type":"application/json","PayPal-Request-Id":idempotency_key,"Prefer":"return=representation"})
        if not isinstance(response,dict):raise RuntimeError("Invalid sandbox capture response")
        units=response.get("purchase_units",[])
        if (response.get("id")!=order_id or response.get("status")!="COMPLETED"
                or not isinstance(units,list) or len(units)!=1 or not isinstance(units[0],dict)):
            raise RuntimeError("Payment response does not match the sandbox order")
        unit=units[0]
        payments=unit.get("payments",{})
        if not isinstance(payments,dict):raise RuntimeError("Invalid sandbox payment response")
        captures=payments.get("captures",[])
        if (unit.get("reference_id")!=request["id"] or not isinstance(captures,list)
                or len(captures)!=1 or not isinstance(captures[0],dict)):
            raise RuntimeError("Payment response does not match the purchase request")
        capture=captures[0]
        if (capture.get("status")!="COMPLETED" or not isinstance(capture.get("id"),str)
                or not re.fullmatch(r"[A-Za-z0-9_-]{6,64}",capture["id"])):
            raise RuntimeError("Payment not completed; no successful capture confirmation")
        amount=capture.get("amount",{})
        if not isinstance(amount,dict) or amount.get("currency_code")!="USD":
            raise RuntimeError("Sandbox capture amount or currency mismatch")
        try:captured_cents=cents_of(amount.get("value"))
        except ValueError as exc:raise RuntimeError("Invalid sandbox capture amount") from exc
        if captured_cents!=request["cents"]:
            raise RuntimeError("Sandbox capture amount or currency mismatch")
        return capture["id"]

class Assessor:
    """Local Ollama supplies advice, NEVER permission to spend."""
    def __init__(self,transport=None,model=None):
        self.http=transport or HttpTransport()
        self.model=model if model is not None else os.getenv("OLLAMA_MODEL","")
    def review(self,item,justification,cents):
        rule="HIGH" if cents>10000 else ("MEDIUM" if cents>3000 else "LOW")
        note=("Amount exceeds $100. Verify price and necessity." if rule=="HIGH" else
              "Amount exceeds $30; check the vendor and budget." if rule=="MEDIUM" else
              "Low purchase amount; verify supplier and intended use.")
        if not self.model:return rule,note,"rules-only (local AI not connected)"
        try:
            result=self.http.post(MODEL_HOST+"/api/generate",{
                "model":self.model,"stream":False,
                "prompt":("You are an advisory spending risk reviewer. Never authorize a purchase. "
                          "Given this JSON, identify one short concrete risk or verification step, <=160 chars. "
                          +json.dumps({"item":item,"reason":justification,"amount_usd":money(cents)}))},
                {"Content-Type":"application/json"})
            response=result.get("response","")
            if isinstance(response,str) and 4<=len(response.strip())<=160:
                if (result.get("runtime")=="Transformers.js 3.8.1 / ONNX Runtime CPU q4"
                        and result.get("model")==self.model and result.get("advisory_only") is True):
                    return rule,response.strip(),"Local CPU Transformers.js "+self.model+" (advisory only)"
                return rule,response.strip(),"Ollama "+self.model+" (advisory only)"
        except (OSError,TimeoutError,ValueError,RuntimeError):pass
        return rule,note,"rules-only (local AI unavailable)"

class Guard:
    def __init__(self,db,paypal=None,assessor=None):
        self.db=db
        self.paypal=paypal or PayPalSandbox()
        self.assessor=assessor or Assessor()
    def request(self,item,justification,amount,source="agent"):
        item=normalized_text(item,"item")
        justification=normalized_text(justification,"justification",500)
        source=normalized_text(source,"source",60)
        cents=cents_of(amount)
        risk,note,mode=self.assessor.review(item,justification,cents)
        created=utcnow()
        return self.db.put({"id":str(uuid.uuid4()),"created":created,"item":item,
             "justification":justification,"cents":cents,"source":source,
             "risk":risk,"risk_note":note,"assessment_mode":mode,"status":"PENDING_REVIEW"})
    def decide(self,request_id,approve):
        if not isinstance(approve,bool):raise ValueError("approve must be boolean")
        return self.db.transition(request_id,"PENDING_REVIEW","APPROVED" if approve else "REJECTED")
    def checkout(self,request_id):
        with self.db.operation_lock:
            request=self.db.get(request_id)
            if not request or request["status"]!="APPROVED":
                raise ValueError("Order cannot be created without separate approval")
            key=self.db.operation_key(request_id,"checkout")
            order_id,url=self.paypal.create(request,key)
            return self.db.transition(request_id,"APPROVED","AWAITING_BUYER",paypal_order_id=order_id,checkout_url=url)
    def capture(self,request_id):
        with self.db.operation_lock:
            req=self.db.get(request_id)
            if not req or req["status"]!="AWAITING_BUYER" or not req.get("paypal_order_id"):
                raise ValueError("Order is not waiting for buyer checkout")
            key=self.db.operation_key(request_id,"capture")
            capture_id=self.paypal.capture(req,key)
            return self.db.transition(request_id,"AWAITING_BUYER","SANDBOX_CAPTURED",capture_id=capture_id)

class AppHandler(BaseHTTPRequestHandler):
    guard=None
    token=None
    reviewer_token=None
    root=Path(__file__).resolve().parent/"static"
    def _send(self,code,body,content_type="application/json; charset=utf-8"):
        data=json.dumps(body).encode() if isinstance(body,(dict,list)) else body
        self.send_response(code)
        self.send_header("Content-Type",content_type)
        self.send_header("Cache-Control","no-store")
        self.send_header("X-Content-Type-Options","nosniff")
        self.send_header("Content-Security-Policy","default-src 'self'; connect-src 'self'; script-src 'self'; style-src 'self'; img-src 'self'")
        self.send_header("Content-Length",str(len(data)))
        self.end_headers();self.wfile.write(data)
    def do_GET(self):
        if not self._valid_host():return self._send(403,{"error":"Invalid local Host"})
        if self.path=="/mcp":
            self.send_response(405);self.send_header("Allow","POST");self.send_header("Content-Length","0");self.end_headers();return
        if self.path=="/":return self._send(200,(self.root/"index.html").read_bytes(),"text/html; charset=utf-8")
        if self.path=="/app.js":return self._send(200,(self.root/"app.js").read_bytes(),"application/javascript; charset=utf-8")
        if self.path=="/styles.css":return self._send(200,(self.root/"styles.css").read_bytes(),"text/css; charset=utf-8")
        if self.path=="/api/state":
            return self._send(200,{"mode":"PAYPAL_SANDBOX_ONLY","credentials_ready":self.guard.paypal.ready(),
                                   "requests":self.guard.db.all(),"csrf":self.token})
        return self._send(404,{"error":"Not found"})
    def do_POST(self):
        try:
            if not self._valid_host():return self._send(403,{"error":"Invalid local Host"})
            if self.path=="/mcp":return self._mcp_post()
            if not self.token or not secrets.compare_digest(self.headers.get("X-CSRF-Token",""),self.token) or self.headers.get("Origin") not in (None,"http://"+self.headers.get("Host")):
                return self._send(403,{"error":"Invalid origin or CSRF token"})
            if self.path in ("/api/decide","/api/checkout","/api/capture"):
                if not self.reviewer_token or not secrets.compare_digest(self.headers.get("X-Reviewer-Token",""),self.reviewer_token):
                    return self._send(403,{"error":"Independent reviewer authorization required"})
            n=int(self.headers.get("Content-Length","0"))
            if n<1 or n>10000:raise ValueError("Invalid request body size")
            body=json.loads(self.rfile.read(n))
            if not isinstance(body,dict):raise ValueError("JSON object required")
            if self.path=="/api/request":
                result=self.guard.request(body.get("item"),body.get("justification"),body.get("amount"),body.get("source","agent"))
            elif self.path=="/api/decide":result=self.guard.decide(body.get("id"),body.get("approve"))
            elif self.path=="/api/checkout":result=self.guard.checkout(body.get("id"))
            elif self.path=="/api/capture":result=self.guard.capture(body.get("id"))
            else:return self._send(404,{"error":"Not found"})
            return self._send(200,result)
        except (ValueError,KeyError,TypeError,json.JSONDecodeError) as e:
            return self._send(400,{"error":str(e)})
        except (RuntimeError,OSError):
            return self._send(503,{"error":"Sandbox service unavailable or payment not yet approved"})

    def _mcp_post(self):
        """Stateless Streamable HTTP MCP surface: propose and inspect, never approve."""
        origin=self.headers.get("Origin")
        if origin not in (None,"http://"+self.headers.get("Host")):
            return self._send(403,{"error":"Invalid local Origin"})
        accept={part.split(";",1)[0].strip().lower() for part in
                self.headers.get("Accept","").split(",")}
        if not {"application/json","text/event-stream"}.issubset(accept):
            return self._send(406,{"error":"Accept must include application/json and text/event-stream"})
        if self.headers.get_content_type()!="application/json":
            return self._send(415,{"error":"application/json required"})
        try:n=int(self.headers.get("Content-Length","0"))
        except ValueError:n=0
        if n<1 or n>10000:return self._send(400,{"error":"Invalid request body size"})
        try:body=json.loads(self.rfile.read(n))
        except (UnicodeDecodeError,json.JSONDecodeError):
            return self._mcp_json({"jsonrpc":"2.0","id":None,"error":{"code":-32700,"message":"Parse error"}})
        if not isinstance(body,dict) or body.get("jsonrpc")!="2.0" or not isinstance(body.get("method"),str):
            return self._mcp_json({"jsonrpc":"2.0","id":body.get("id") if isinstance(body,dict) else None,
                                   "error":{"code":-32600,"message":"Invalid Request"}})
        request_id=body.get("id")
        if request_id is None:
            # MCP notifications have no response body.
            self.send_response(202);self.send_header("Content-Length","0");self.end_headers();return
        method=body["method"]
        params=body.get("params") or {}
        if not isinstance(params,dict):
            return self._mcp_json({"jsonrpc":"2.0","id":request_id,
                                   "error":{"code":-32602,"message":"params must be an object"}})
        if method!="initialize" and self.headers.get("MCP-Protocol-Version") not in MCP_PROTOCOL_VERSIONS:
            return self._send(400,{"error":"A supported MCP-Protocol-Version header is required"})
        try:
            if method=="initialize":
                requested=params.get("protocolVersion")
                version=requested if requested in MCP_PROTOCOL_VERSIONS else "2025-11-25"
                result={"protocolVersion":version,"capabilities":{"tools":{"listChanged":False}},
                        "serverInfo":{"name":"agentcheckout-guard","version":"0.3.0"},
                        "instructions":"Draft and inspect purchase requests. Every request stays pending until a separate reviewer approves it in AgentCheckout Guard. This MCP server cannot approve, create checkout, or capture payment."}
            elif method=="ping":result={}
            elif method=="tools/list":result={"tools":self._mcp_tools()}
            elif method=="tools/call":result=self._mcp_call(params)
            else:
                return self._mcp_json({"jsonrpc":"2.0","id":request_id,
                                       "error":{"code":-32601,"message":"Method not found"}})
            return self._mcp_json({"jsonrpc":"2.0","id":request_id,"result":result})
        except (ValueError,KeyError,TypeError) as error:
            if method=="tools/call":
                result={"content":[{"type":"text","text":str(error)}],"isError":True}
                return self._mcp_json({"jsonrpc":"2.0","id":request_id,"result":result})
            return self._mcp_json({"jsonrpc":"2.0","id":request_id,
                                   "error":{"code":-32602,"message":str(error)}})

    @staticmethod
    def _mcp_tools():
        return [
            {"name":"propose_purchase","description":"Create a purchase proposal for independent human review. This never approves or charges.",
             "inputSchema":{"type":"object","properties":{"item":{"type":"string","minLength":1,"maxLength":200},
                 "justification":{"type":"string","minLength":1,"maxLength":500},
                 "amount":{"type":"number","minimum":0.01,"maximum":500}},
                 "required":["item","justification","amount"],"additionalProperties":False}},
            {"name":"list_purchase_requests","description":"Inspect recent purchase proposals and review status. No approval or payment action is available.",
             "inputSchema":{"type":"object","properties":{},"additionalProperties":False}}
        ]

    def _mcp_call(self,params):
        if not isinstance(params,dict):raise ValueError("Tool parameters must be an object")
        name=params.get("name");args=params.get("arguments") or {}
        if not isinstance(args,dict):raise ValueError("Tool arguments must be an object")
        if name=="propose_purchase":
            if set(args)!={"item","justification","amount"}:
                raise ValueError("Provide only item, justification, and amount")
            if not isinstance(args["amount"],(int,float)) or isinstance(args["amount"],bool):
                raise ValueError("amount must be a number")
            request=self.guard.request(args["item"],args["justification"],args["amount"],"alexa_plus_mcp")
            data={k:request[k] for k in ("id","created","item","justification","cents","risk","risk_note","assessment_mode","status")}
        elif name=="list_purchase_requests":
            if args:raise ValueError("This tool takes no arguments")
            data=[{k:item[k] for k in ("id","created","item","justification","cents","risk","risk_note","assessment_mode","status")}
                  for item in self.guard.db.all()]
        else:raise ValueError("Unknown tool")
        return {"content":[{"type":"text","text":json.dumps(data,sort_keys=True)}],"structuredContent":{"data":data},"isError":False}

    def _mcp_json(self,payload):
        return self._send(200,payload)
    def log_message(self,*args):pass
    def _valid_host(self):
        hosts=self.headers.get_all("Host",[])
        return len(hosts)==1 and hosts[0] in (f"127.0.0.1:{self.server.server_port}",f"localhost:{self.server.server_port}")

def reviewer_secret(path):
    """Reviewer-controlled local file; never disclosed by state APIs or logs."""
    supplied=os.getenv("AGENTCHECKOUT_REVIEWER_TOKEN")
    if supplied:
        if len(supplied)<32:raise ValueError("Reviewer token needs at least 32 characters")
        return supplied
    path=Path(path)
    try:
        fd=os.open(path,os.O_WRONLY|os.O_CREAT|os.O_EXCL,0o600)
    except FileExistsError:
        if path.is_symlink() or path.stat().st_mode & 0o077:
            raise ValueError("Reviewer token file must be private and not a symlink")
        value=path.read_text().strip()
        if len(value)<32:raise ValueError("Reviewer token needs at least 32 characters")
        return value
    value=secrets.token_urlsafe(32)
    with os.fdopen(fd,"w") as stream:stream.write(value+"\n")
    return value

def serve(port=8766,db_path="agentcheckout.db",reviewer_token_path=None):
    AppHandler.guard=Guard(Database(db_path))
    AppHandler.token=secrets.token_urlsafe(32)
    AppHandler.reviewer_token=reviewer_secret(reviewer_token_path or str(db_path)+".reviewer.key")
    http=ThreadingHTTPServer(("127.0.0.1",port),AppHandler)
    print(f"AgentCheckout Guard: http://127.0.0.1:{port} (sandbox only)")
    try:http.serve_forever()
    finally:http.server_close()

if __name__=="__main__":
    import argparse
    p=argparse.ArgumentParser(description="AgentCheckout Guard: no live money supported")
    p.add_argument("--port",type=int,default=8766)
    p.add_argument("--db",default="agentcheckout.db")
    p.add_argument("--reviewer-token-file",help="Private reviewer-controlled token file (created with mode 0600)")
    a=p.parse_args()
    serve(a.port,a.db,a.reviewer_token_file)

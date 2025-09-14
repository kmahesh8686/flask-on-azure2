from flask import Flask, request, jsonify, redirect, url_for, session, render_template_string
from flask_cors import CORS
from datetime import datetime
import zoneinfo, time

app = Flask(__name__)
app.secret_key = "SUPERSECRETKEY"   # change in production
CORS(app)

IST = zoneinfo.ZoneInfo("Asia/Kolkata")

# =========================
# Predefined Tokens & Passwords
# =========================
PREDEFINED_TOKENS = ["km8686", "kmk8686", "km5630"]
token_passwords = {t: "12345678" for t in PREDEFINED_TOKENS}

# Admin password
ADMIN_PASSWORD = "12345678"

# Mobile caps per token (None = unlimited)
token_mobile_caps = {t: None for t in PREDEFINED_TOKENS}
token_processed_mobiles = {t: set() for t in PREDEFINED_TOKENS}

# =========================
# Storage
# =========================
mobile_otps = {t: [] for t in PREDEFINED_TOKENS}
vehicle_otps = {t: [] for t in PREDEFINED_TOKENS}
otp_data = {t: [] for t in PREDEFINED_TOKENS}
client_sessions = {t: {} for t in PREDEFINED_TOKENS}
browser_queues = {t: {} for t in PREDEFINED_TOKENS}
login_sessions = {t: {} for t in PREDEFINED_TOKENS}

BROWSER_STALE_SECONDS = 10.0

# =========================
# Helpers
# =========================
def valid_token(token): return token in PREDEFINED_TOKENS

def add_browser_to_queue(token, identifier, browser_id):
    queues = browser_queues[token]; sessions = client_sessions[token]
    if identifier not in queues: queues[identifier] = []
    if browser_id not in queues[identifier]:
        queues[identifier].append(browser_id)
        sessions[(identifier, browser_id)] = {
            "first_request": datetime.now(IST),
            "last_request": time.time()
        }
    else: sessions[(identifier, browser_id)]["last_request"] = time.time()

def get_next_browser(token, identifier):
    q = browser_queues[token]
    return q[identifier][0] if identifier in q and q[identifier] else None

def pop_browser_from_queue(token, identifier):
    q = browser_queues[token]
    if identifier in q and q[identifier]: q[identifier].pop(0)

def mark_otp_removed_to_data(token, entry, reason="stale_browser", browser_id=None):
    r = entry.copy(); r["removed_at"] = datetime.now(IST); r["removed_reason"] = reason
    if browser_id: r["browser_id"] = browser_id
    otp_data[token].append(r)

def cleanup_stale_browsers_and_handle_pending(token, identifier):
    now_ts = time.time(); q = browser_queues[token]; s = client_sessions[token]
    if identifier not in q: return
    for b in list(q[identifier]):
        sess = s.get((identifier, b))
        if not sess or now_ts - sess.get("last_request", 0) > BROWSER_STALE_SECONDS:
            try: q[identifier].remove(b)
            except: pass
            s.pop((identifier, b), None)

# =========================
# OTP APIs
# =========================
@app.route("/api/receive-otp", methods=["POST"])
def receive_otp():
    d = request.get_json(force=True); otp = (d.get("otp") or "").strip()
    token = (d.get("token") or "").strip(); sim = (d.get("sim_number") or "").strip().upper()
    vehicle = (d.get("vehicle") or "").strip().upper()
    if not otp or not token: return jsonify(status="error", message="OTP and token required"), 400
    if not valid_token(token): return jsonify(status="error", message="Invalid token"), 403

    if not vehicle:
        if sim not in token_processed_mobiles[token]:
            cap = token_mobile_caps[token]
            if cap is not None and len(token_processed_mobiles[token]) >= cap:
                otp_data[token].append({"otp": otp,"token": token,"sim_number": sim,
                    "timestamp": datetime.now(IST),"removed_reason":"limit_exceeded"})
                return jsonify(status="success", message="OTP stored"), 200
            token_processed_mobiles[token].add(sim)

    e = {"otp": otp,"token": token,"timestamp": datetime.now(IST)}
    if vehicle: e["vehicle"]=vehicle; vehicle_otps[token].append(e)
    else: e["sim_number"]=sim or "UNKNOWNSIM"; mobile_otps[token].append(e)
    return jsonify(status="success", message="OTP stored"), 200

@app.route("/api/get-latest-otp")
def get_latest_otp():
    token=(request.args.get("token") or "").strip(); sim=(request.args.get("sim_number") or "").strip().upper()
    vehicle=(request.args.get("vehicle") or "").strip().upper(); bid=(request.args.get("browser_id") or "").strip()
    if not token or (not sim and not vehicle) or not bid: return jsonify(status="error", message="token + sim_number/vehicle + browser_id required"),400
    if not valid_token(token): return jsonify(status="error", message="Invalid token"),403
    idf=sim if sim else vehicle; add_browser_to_queue(token,idf,bid)
    ck=(idf,bid); client_sessions[token][ck]["last_request"]=time.time()
    cleanup_stale_browsers_and_handle_pending(token,idf)
    se=client_sessions[token].get(ck); 
    if not se: return jsonify(status="waiting"),200
    st=se["first_request"]; nb=get_next_browser(token,idf)
    if vehicle:
        new=[o for o in vehicle_otps[token] if o["vehicle"]==vehicle and o["timestamp"]>st]
        if new and nb==bid: lat=new[0]; vehicle_otps[token].remove(lat); lat["browser_id"]=bid; otp_data[token].append(lat); pop_browser_from_queue(token,idf); client_sessions[token].pop(ck,None); return jsonify(status="success",otp=lat["otp"],vehicle=lat["vehicle"],browser_id=bid,timestamp=lat["timestamp"].strftime("%Y-%m-%d %H:%M:%S")),200
        return jsonify(status="waiting"),200
    else:
        if [o for o in otp_data[token] if o.get("sim_number")==sim and o.get("removed_reason")=="limit_exceeded"]:
            return jsonify(status="error", message="limit_exceeded"),403
        new=[o for o in mobile_otps[token] if o["sim_number"]==sim and o["timestamp"]>st]
        if new and nb==bid: lat=new[0]; mobile_otps[token].remove(lat); lat["browser_id"]=bid; otp_data[token].append(lat); pop_browser_from_queue(token,idf); client_sessions[token].pop(ck,None); return jsonify(status="success",otp=lat["otp"],sim_number=lat["sim_number"],browser_id=bid,timestamp=lat["timestamp"].strftime("%Y-%m-%d %H:%M:%S")),200
        return jsonify(status="waiting"),200

# =========================
# Login detection APIs
# =========================
@app.route("/api/login-detect",methods=["POST"])
def login_detect():
    d=request.get_json(force=True); token=(d.get("token") or "").strip()
    mob=(d.get("mobile_number") or "").strip().upper(); src=(d.get("source") or "").strip().upper()
    if not mob or not token: return jsonify(status="error",message="mobile_number and token required"),400
    if not valid_token(token): return jsonify(status="error",message="Invalid token"),403
    login_sessions[token].setdefault(mob,[]).append({"timestamp":datetime.now(IST),"source":src})
    return jsonify(status="success",message="Login detected"),200

@app.route("/api/login-found")
def login_found():
    token=(request.args.get("token") or "").strip(); mob=(request.args.get("mobile_number") or "").strip().upper()
    if not token or not mob: return jsonify(status="error",message="token + mobile_number required"),400
    if not valid_token(token): return jsonify(status="error",message="Invalid token"),403
    if mob in login_sessions[token]:
        det=[{"timestamp":e["timestamp"].strftime("%Y-%m-%d %H:%M:%S"),"source":e.get("source","")} for e in login_sessions[token][mob]]
        return jsonify(status="found",mobile_number=mob,detections=det),200
    return jsonify(status="not_found",mobile_number=mob),200

# =========================
# Token dashboard (embed support)
# =========================
def render_token_panel(token, section):
    # OTP table
    if section=="otp":
        rows="".join(f"<tr><td>{e.get('sim_number','')}</td><td>{e.get('vehicle','')}</td><td>{e.get('otp','')}</td><td>{e.get('browser_id','')}</td><td>{e.get('timestamp',e.get('removed_at')).strftime('%Y-%m-%d %H:%M:%S')}</td><td>{e.get('removed_reason','')}</td></tr>" for e in otp_data[token])
        return f"<h3>OTP Data ({token})</h3><table border=1><tr><th>Mobile</th><th>Vehicle</th><th>OTP</th><th>Browser</th><th>Date</th><th>Reason</th></tr>{rows or '<tr><td colspan=6>No data</td></tr>'}</table>"
    if section=="login":
        rows="".join(f"<tr><td>{m}</td><td>{e['timestamp'].strftime('%Y-%m-%d %H:%M:%S')}</td><td>{e.get('source','')}</td></tr>" for m,es in login_sessions[token].items() for e in es)
        return f"<h3>Login Detections ({token})</h3><table border=1><tr><th>Mobile</th><th>Date</th><th>Source</th></tr>{rows or '<tr><td colspan=3>No logins</td></tr>'}</table>"
    if section=="change_password":
        return f"<h3>Change Password ({token})</h3><form method='POST' action='/change-password/{token}'><input type=password name=current_password placeholder='Current'><br><input type=password name=new_password placeholder='New'><br><input type=password name=confirm_password placeholder='Confirm'><br><button>Change</button></form>"
    return "<p>Invalid section</p>"

@app.route("/status/<token>")
def status(token):
    if not (("token" in session and session["token"]==token) or session.get("is_admin")):
        return redirect(url_for("login"))
    embed=request.args.get("embed"); sec=request.args.get("section","otp")
    if embed: return render_token_panel(token,sec)
    return f"<html><body><h2>{token} Dashboard (use admin embed mode)</h2></body></html>"

# =========================
# Admin dashboard
# =========================
@app.route("/admin",methods=["GET","POST"])
def admin():
    if not session.get("is_admin"): return redirect(url_for("admin_login"))
    # Sidebar links use JS fetch
    token_links="".join(f"<li><a href='#' onclick=\"loadContent('/status/{t}?embed=1&section=otp')\">{t}</a></li>" for t in PREDEFINED_TOKENS)
    html=f"""
    <html><head><script>
    function loadContent(u){{fetch(u).then(r=>r.text()).then(h=>{{document.getElementById('content_panel').innerHTML=h;}});}}
    </script></head>
    <body><div style='display:flex;'>
      <div style='width:200px;background:#2c3e50;color:white;padding:20px;'>
        <h3>Admin</h3>
        <h4>Tokens</h4><ul>{token_links}</ul>
        <h4>Sections</h4>
        <ul>
          <li><a href='#' onclick="loadContent('/status/km8686?embed=1&section=otp')">OTP Data</a></li>
          <li><a href='#' onclick="loadContent('/status/km8686?embed=1&section=login')">Login Detections</a></li>
          <li><a href='#' onclick="loadContent('/status/km8686?embed=1&section=change_password')">Change Password</a></li>
        </ul>
      </div>
      <div id='content_panel' style='flex-grow:1;padding:20px;'>
        <h2>Welcome Admin</h2><p>Select a token or section</p>
      </div>
    </div></body></html>
    """
    return html

@app.route("/admin-login",methods=["GET","POST"])
def admin_login():
    global ADMIN_PASSWORD
    if request.method=="POST":
        u=request.form.get("username","").upper(); p=request.form.get("password","")
        if u!="ADMIN": return "wrong user"
        if p!=ADMIN_PASSWORD: return "wrong pass"
        session["is_admin"]=True; return redirect(url_for("admin"))
    return "<form method=post><input name=username><input name=password type=password><button>Login</button></form>"

# =========================
# Token login
# =========================
@app.route("/login",methods=["GET","POST"])
def login():
    if request.method=="POST":
        t=request.form.get("token",""); p=request.form.get("password","")
        if t not in PREDEFINED_TOKENS: return "wrong token"
        if p!=token_passwords[t]: return "wrong pass"
        session["token"]=t; return redirect(url_for("status",token=t))
    return "<form method=post><input name=token><input name=password type=password><button>Login</button></form>"

@app.route("/logout")
def logout(): session.pop("token",None); return redirect(url_for("login"))

@app.route("/change-password/<token>",methods=["POST"])
def change_password(token):
    if "token" not in session or session["token"]!=token: return redirect(url_for("login"))
    cur=request.form.get("current_password"); new=request.form.get("new_password"); conf=request.form.get("confirm_password")
    if cur!=token_passwords[token]: return "wrong current"
    if new!=conf: return "no match"
    token_passwords[token]=new; return "changed"

# =========================
if __name__=="__main__": app.run(debug=True)

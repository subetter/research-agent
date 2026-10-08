"use client";
import { useEffect, useState } from "react";
import { Layers, MessageSquare, Compass, ShieldCheck, LogOut, Loader2, ArrowRight, Eye, EyeOff } from "lucide-react";
import ResearchWorkbench from "../components/research-workbench";
import ChatWorkspace from "../components/chat-workspace";
import AdminWorkspace from "../components/admin-workspace";
import { api, User } from "../components/api";

export default function App() {
  const [user, setUser] = useState<User | null>(null);
  const [loading, setLoading] = useState(true);
  const [needsAdmin, setNeedsAdmin] = useState(false);
  const [screen, setScreen] = useState<"chat" | "research" | "admin">("chat");
  const [form, setForm] = useState<"login" | "register">("login");
  const [username, setUsername] = useState("");
  const [displayName, setDisplayName] = useState("");
  const [password, setPassword] = useState("");
  const [visiblePassword, setVisiblePassword] = useState(false);
  const [error, setError] = useState("");
  const [busy, setBusy] = useState(false);

  useEffect(() => {
    let active = true;
    Promise.all([api<{user: User}>("/auth/me").catch(() => null), api<{needs_admin: boolean}>("/auth/bootstrap")]).then(([session, bootstrap]) => {
      if (!active) return;
      setUser(session?.user || null); setNeedsAdmin(bootstrap.needs_admin);
      if (bootstrap.needs_admin) setForm("register");
    }).catch(e => {if (active) setError(e.message);}).finally(() => {if (active) setLoading(false);});
    const expired = () => {setUser(null); setScreen("chat"); setError("登录已失效，请重新登录。");};
    window.addEventListener("auth-expired", expired);
    return () => {active = false; window.removeEventListener("auth-expired", expired);};
  }, []);

  async function authenticate(e: React.FormEvent) {
    e.preventDefault(); setBusy(true); setError("");
    try {
      const result = await api<{user: User}>(`/auth/${form}`, {method: "POST", body: JSON.stringify({username, password, ...(form === "register" ? {display_name: displayName} : {})})});
      setUser(result.user); setPassword(""); setNeedsAdmin(false); setScreen("chat");
    } catch(e) {setError(e instanceof Error ? e.message : "登录失败");} finally {setBusy(false);}
  }

  async function logout() {
    setBusy(true); setError("");
    try {await api("/auth/logout", {method: "POST"}); setUser(null); setPassword(""); setScreen("chat");}
    catch(e) {setError(e instanceof Error ? e.message : "退出失败");} finally {setBusy(false);}
  }

  if (loading) return <div className="app-loading"><Loader2 className="spin" size={23}/><p>正在打开你的工作空间…</p></div>;
  if (!user) return <div className="auth-page"><div className="auth-story"><div className="auth-brand"><Layers size={27}/><span>Research Workbench</span></div><p className="eyebrow">THINK. EXPLORE. DISCOVER.</p><h1>从一次对话，<br/>到一份有依据的洞察。</h1><p className="auth-description">日常问答、深度研究和项目资料，<br/>在属于你的空间里持续积累。</p><div className="auth-capabilities"><span><MessageSquare size={17}/> 多轮会话</span><span><Compass size={17}/> 深度研究</span><span><ShieldCheck size={17}/> 账号隔离</span></div><p className="auth-story-footer">个人工作空间 · 管理员只读审阅 · v0.2</p></div><div className="auth-panel"><form className="auth-card" onSubmit={authenticate}><div className="auth-mobile-brand"><Layers size={22}/> Research Workbench</div><h2>{needsAdmin ? "创建管理员账号" : form === "login" ? "欢迎回来" : "创建你的账号"}</h2><p>{needsAdmin ? "这是首个账号，将拥有管理员权限，并接收已有本地研究记录。" : form === "login" ? "登录后继续你的会话与研究。" : "会话、项目和研究记录按账号保存。"}</p><div className="auth-tabs"><button type="button" className={form === "login" ? "active" : ""} onClick={() => {setForm("login"); setError("");}}>登录</button><button type="button" className={form === "register" ? "active" : ""} onClick={() => {setForm("register"); setError("");}}>注册</button></div>{error && <div role="alert" className="inline-warning">{error}</div>}{form === "register" && <label>显示名称<input aria-label="显示名称" required maxLength={60} value={displayName} onChange={e => setDisplayName(e.target.value)} placeholder="怎么称呼你" autoComplete="name"/></label>}<label>用户名<input aria-label="用户名" required pattern="[a-zA-Z0-9_.-]{3,32}" value={username} onChange={e => setUsername(e.target.value)} placeholder="3–32 位英文、数字或 _ . -" autoComplete="username"/></label><label>密码<div className="password-field"><input aria-label="密码" type={visiblePassword ? "text" : "password"} required minLength={form === "register" ? 10 : 1} maxLength={128} value={password} onChange={e => setPassword(e.target.value)} placeholder={form === "register" ? "至少 10 位" : "输入密码"} autoComplete={form === "register" ? "new-password" : "current-password"}/><button type="button" aria-label={visiblePassword ? "隐藏密码" : "显示密码"} onClick={() => setVisiblePassword(v => !v)}>{visiblePassword ? <EyeOff size={16}/> : <Eye size={16}/>}</button></div></label><button className="primary auth-submit" disabled={busy}>{busy ? <Loader2 size={16} className="spin"/> : <ArrowRight size={16}/>} {form === "login" ? "进入工作空间" : "注册并进入"}</button><p className="auth-notice">账号的聊天与研究记录可由管理员在只读后台查看。</p></form></div></div>;

  return <div className="account-app"><header className="account-bar"><div className="account-logo"><Layers size={18}/><strong>Research</strong><span>WORKBENCH</span></div><nav><button className={screen === "chat" ? "active" : ""} onClick={() => setScreen("chat")}><MessageSquare size={15}/> 普通会话</button><button className={screen === "research" ? "active" : ""} onClick={() => setScreen("research")}><Compass size={15}/> 深度研究</button>{user.role === "admin" && <button className={screen === "admin" ? "active" : ""} onClick={() => setScreen("admin")}><ShieldCheck size={15}/> 管理员</button>}</nav><div className="account-user"><span>{user.display_name}</span><small>{user.role === "admin" ? "管理员" : "用户"}</small><button aria-label="退出登录" title="退出登录" disabled={busy} onClick={logout}><LogOut size={16}/></button></div></header>{error && <div className="error-banner" role="alert">{error}</div>}{screen === "chat" && <ChatWorkspace key={user.id} user={user}/>}<div className="research-container" style={{display: screen === "research" ? "block" : "none"}}><ResearchWorkbench key={user.id}/></div>{screen === "admin" && user.role === "admin" && <AdminWorkspace/>}</div>;
}

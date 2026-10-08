export class ApiError extends Error {
  constructor(public status: number, message: string) { super(message); }
}

export async function api<T>(path: string, options?: RequestInit): Promise<T> {
  const response = await fetch(`/api${path}`, {credentials: "same-origin", headers: {"Content-Type": "application/json"}, ...options});
  if (!response.ok) {
    let message = `请求失败 (${response.status})`;
    try {const body = await response.json(); message = typeof body.detail === "string" ? body.detail : "输入参数无效，请检查后重试";} catch {}
    if (response.status === 401 && !path.startsWith("/auth/")) window.dispatchEvent(new Event("auth-expired"));
    throw new ApiError(response.status, message);
  }
  return response.json();
}

export type User = {id: string; username: string; display_name: string; role: "admin" | "user"; created_at: string};

export async function streamApi(path: string, body: unknown, onEvent: (event: string, data: unknown) => void) {
  const response = await fetch(`/api${path}`, {method: "POST", credentials: "same-origin", headers: {"Content-Type": "application/json"}, body: JSON.stringify(body)});
  if (!response.ok) {
    if (response.status === 401) window.dispatchEvent(new Event("auth-expired"));
    const result = await response.json().catch(() => ({}));
    throw new Error(typeof result.detail === "string" ? result.detail : `请求失败 (${response.status})`);
  }
  if (!response.body) throw new Error("当前浏览器无法读取流式回复");
  const reader = response.body.getReader();
  const decoder = new TextDecoder();
  let buffer = "", terminal = false;
  const consume = (frame: string) => {
    let event = "message";
    const data: string[] = [];
    for (const line of frame.split(/\r?\n/)) {
      if (line.startsWith("event:")) event = line.slice(6).trim();
      if (line.startsWith("data:")) data.push(line.slice(5).trimStart());
    }
    if (!data.length) return;
    onEvent(event, JSON.parse(data.join("\n")));
    if (event === "done" || event === "error") terminal = true;
  };
  try {
    while (true) {
      const {value, done} = await reader.read();
      buffer += decoder.decode(value, {stream: !done});
      let match: RegExpExecArray | null;
      while ((match = /\r?\n\r?\n/.exec(buffer))) {
        consume(buffer.slice(0, match.index));
        buffer = buffer.slice(match.index + match[0].length);
      }
      if (done) break;
    }
    if (buffer.trim()) consume(buffer);
    if (!terminal) throw new Error("生成连接中断；已收到的内容保存在会话记录中");
  } finally {
    await reader.cancel().catch(() => {});
    reader.releaseLock();
  }
}

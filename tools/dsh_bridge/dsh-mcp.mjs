#!/usr/bin/env node
/**
 * dsh-mcp.mjs — 把 DeepSeek Harness (dsh) 暴露为一个 MCP stdio server，
 * 让 Codex / Claude Code / 任何 MCP 客户端用工具调用驱动 dsh。
 *
 * 环境变量：
 *   DSH_BIN        dsh 可执行文件，可带参数，如 "node /path/apps/cli/lib/bin.js"（默认 "dsh"）
 *   DSH_PROFILE    使用的 dsh profile（默认 "headless"）
 *   DSH_TIMEOUT_MS 单次运行超时，0 表示不限制（默认 30 分钟）
 */
import { spawn } from 'node:child_process'

const [DSH_CMD, ...DSH_PRE] = (process.env.DSH_BIN || 'dsh').split(/\s+/).filter(Boolean)
const PROFILE = process.env.DSH_BRIDGE_PROFILE || 'headless'
const TIMEOUT_MS = Number(process.env.DSH_TIMEOUT_MS || 1_800_000)

const TOOL = {
  name: 'dsh_run',
  description:
    '把一条自包含任务交给 DeepSeek Harness (dsh) 在指定工作目录执行，返回它的最终答复。' +
    '把上次返回的 session_id 传回来即可继续同一段 dsh 会话。',
  inputSchema: {
    type: 'object',
    properties: {
      task: { type: 'string', description: '任务原文，像用户直接输入一样。' },
      cwd: { type: 'string', description: 'dsh 运行的绝对工作目录，通常传当前项目根目录。' },
      session_id: { type: 'string', description: '可选：要继续的 dsh 会话 id。' },
    },
    required: ['task'],
    additionalProperties: false,
  },
}

const send = (msg) => process.stdout.write(JSON.stringify(msg) + '\n')

function runDsh({ task, cwd, session_id }) {
  return new Promise((resolve) => {
    const args = [...DSH_PRE, '--profile', PROFILE, '--json', ...(session_id ? ['--session-id', session_id] : [])]
    const child = spawn(DSH_CMD, args, { cwd: cwd || process.env.DSH_WORKDIR || process.cwd(), env: process.env, stdio: ['pipe', 'pipe', 'pipe'] })
    let out = ''
    let err = ''
    const timer = TIMEOUT_MS > 0 ? setTimeout(() => { err += `\ndsh: timeout after ${TIMEOUT_MS} ms`; child.kill('SIGKILL') }, TIMEOUT_MS) : null
    child.stdout.on('data', (d) => { out += d })
    child.stderr.on('data', (d) => { err += d })
    child.on('error', (e) => { clearTimeout(timer); resolve({ code: -1, text: '', stderr: String(e) }) })
    child.on('close', (code) => {
      clearTimeout(timer)
      const events = out.split('\n').filter((l) => l.trim()).flatMap((l) => { try { return [JSON.parse(l)] } catch { return [] } })
      const final = [...events].reverse().find((e) => e.type === 'final')
      resolve({
        code,
        text: final?.text ?? '',
        sessionId: events.find((e) => e.type === 'session')?.sessionId,
        error: events.find((e) => e.type === 'error')?.message,
        stderr: err.trim().slice(-4000),
      })
    })
    child.stdin.end(task) // 任务走 stdin，避免命令行参数被当作选项、也绕开长度上限
  })
}

async function handle(msg) {
  if (msg.method === 'initialize') {
    send({ jsonrpc: '2.0', id: msg.id, result: {
      protocolVersion: msg.params?.protocolVersion ?? '2025-06-18',
      capabilities: { tools: {} },
      serverInfo: { name: 'dsh-bridge', version: '0.1.0' },
    } })
  } else if (msg.method === 'notifications/initialized') {
    // notification：不回包
  } else if (msg.method === 'tools/list') {
    send({ jsonrpc: '2.0', id: msg.id, result: { tools: [TOOL] } })
  } else if (msg.method === 'tools/call') {
    const args = msg.params?.arguments ?? {}
    if (!args.task) {
      send({ jsonrpc: '2.0', id: msg.id, result: { content: [{ type: 'text', text: '缺少必填参数：task' }], isError: true } })
      return
    }
    const r = await runDsh(args)
    const ok = r.code === 0 && r.text.trim() !== ''
    const text = ok
      ? (r.sessionId ? `${r.text}\n\n[dsh session_id: ${r.sessionId}]` : r.text)
      : `DSH 运行失败（exit ${r.code}）${r.error ? `: ${r.error}` : ''}\n${r.stderr}`.trim()
    send({ jsonrpc: '2.0', id: msg.id, result: { content: [{ type: 'text', text }], isError: !ok, _meta: { sessionId: r.sessionId ?? null, exitCode: r.code } } })
  } else if (msg.id !== undefined) {
    send({ jsonrpc: '2.0', id: msg.id, error: { code: -32601, message: `method not found: ${msg.method}` } })
  }
}

let buf = ''
process.stdin.setEncoding('utf8')
process.stdin.on('data', (chunk) => {
  buf += chunk
  for (let i; (i = buf.indexOf('\n')) >= 0;) {
    const line = buf.slice(0, i)
    buf = buf.slice(i + 1)
    if (!line.trim()) continue
    handle(JSON.parse(line)).catch((e) => send({ jsonrpc: '2.0', id: null, error: { code: -32603, message: String(e) } }))
  }
})

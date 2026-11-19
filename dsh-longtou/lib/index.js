// dsh-longtou DSH 插件入口：只保留 longtou_report 工具。
// 面板 UI（浮标/HTML 面板）已从 DSH 页面内移除，唯一入口是独立窗口：
//   tools\open_panel.ps1（panel_server.py，127.0.0.1:8765）
import { execFile } from 'node:child_process'
import { promisify } from 'node:util'
import path from 'node:path'
import { fileURLToPath } from 'node:url'

const execFileAsync = promisify(execFile)
const ROOT = path.resolve(path.dirname(fileURLToPath(import.meta.url)), '..')
const PYTHON = process.env.DSH_LONGTOU_PYTHON || path.join(ROOT, '.venv', 'Scripts', 'python.exe')
const BRIDGE = path.join(ROOT, 'tools', 'dsh_bridge.py')

async function runBridge(date, stages) {
  const { stdout } = await execFileAsync(
    PYTHON,
    [BRIDGE, 'report', '--date', String(date), '--stages', String(stages)],
    {
      cwd: ROOT,
      timeout: 180000,
      maxBuffer: 20 * 1024 * 1024,
      env: { ...process.env, PYTHONIOENCODING: 'utf-8', PYTHONUTF8: '1' },
    },
  )
  return stdout
}

const reportTool = {
  name: 'longtou_report',
  description:
    'A-share 2→3 / 3→4 / 4→5 leader report (4→5 起，更高阶段随当日天梯自动扩展). Reads local TDX daily bars, eltdx historical 09:25 auction, historical circulating shares, cap-tier gates, turnover, limit-down intensity, leader-state downgrades, and returns a Markdown candidate table with reasons. Research only; no orders.',
  parameters: {
    type: 'object',
    properties: {
      date: {
        type: 'string',
        description: 'T-1 close date, YYYYMMDD (e.g. 20260828).',
      },
      stages: {
        type: 'string',
        description: 'Comma-separated stages: 2,3,4. Default 2,3,4.',
      },
    },
    required: ['date'],
    additionalProperties: false,
  },
  output: {
    schema: { type: 'string' },
    render: (_args, value) => [{ type: 'text', text: String(value) }],
  },
  timeoutMs: 190000,
  async execute(args) {
    const date = String(args.date || '').trim()
    if (!/^\d{8}$/.test(date)) throw new Error('date must be YYYYMMDD')
    const stages = String(args.stages || '2,3,4').trim() || '2,3,4'
    return await runBridge(date, stages)
  },
}

export const name = 'dsh-longtou'
export const inject = ['tools']

export function apply(ctx) {
  ctx.tools.register(reportTool)
}

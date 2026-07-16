import { useState } from 'react'
import {
  ArrowRight,
  BookOpen,
  Clock3,
  FileText,
  Gavel,
  Scale,
  ShieldCheck,
  Users,
} from 'lucide-react'

type UserRole = 'prosecution' | 'defense'

const roleLabels: Record<UserRole, string> = {
  prosecution: '公诉方',
  defense: '辩护方',
}

function CaseLobby({ onStart }: { onStart: (role: UserRole) => void }) {
  const [role, setRole] = useState<UserRole>('prosecution')

  return (
    <main className="lobby-shell">
      <header className="brand-bar">
        <a className="brand" href="/" aria-label="MootCourt Lab 首页">
          <Scale aria-hidden="true" size={22} />
          <span>MootCourt Lab</span>
        </a>
        <span className="environment-tag">TECH DEMO</span>
      </header>

      <section className="case-intro" aria-labelledby="case-title">
        <div className="case-heading">
          <p className="eyebrow">MVP 虚构案件 01</p>
          <h1 id="case-title">青禾影像器材失窃案</h1>
          <p className="case-summary">
            围绕一台专业相机被取走后是否具有非法占有目的，通过举证、质证与证人询问完成一次简化的一审庭审训练。
          </p>
          <div className="case-meta" aria-label="案件信息">
            <span><Clock3 size={17} />预计 20-30 分钟</span>
            <span><Users size={17} />1 名被告人 · 3 名证人</span>
            <span><FileText size={17} />教学化简化程序</span>
          </div>
        </div>

        <div className="case-facts" aria-label="训练重点">
          <div><strong>案由</strong><span>盗窃罪（教学模拟）</span></div>
          <div><strong>法域</strong><span>上海市 · 刑事一审教学模拟</span></div>
          <div><strong>状态</strong><span>开发案卷已就绪 · 仅输出模拟分析</span></div>
        </div>
      </section>

      <section className="role-band" aria-labelledby="role-title">
        <div className="role-copy">
          <p className="eyebrow">选择庭审席位</p>
          <h2 id="role-title">你将在本庭承担哪一方？</h2>
          <p>系统只向你展示该角色有权查阅的案卷材料，另一方和证人由 AI 补齐。</p>
        </div>

        <div className="role-controls">
          <div className="segmented" role="radiogroup" aria-label="庭审角色">
            {(['prosecution', 'defense'] as const).map((item) => (
              <button
                className={role === item ? 'segment active' : 'segment'}
                key={item}
                onClick={() => setRole(item)}
                role="radio"
                aria-checked={role === item}
              >
                {item === 'prosecution' ? <Gavel size={19} /> : <ShieldCheck size={19} />}
                {roleLabels[item]}
              </button>
            ))}
          </div>
          <button className="primary-action" onClick={() => onStart(role)}>
            开始庭审 <ArrowRight size={19} />
          </button>
        </div>
      </section>

      <aside className="disclaimer">
        <ShieldCheck size={19} aria-hidden="true" />
        <p><strong>教学用途声明</strong> 本系统仅处理虚构案卷，用于庭审训练，不构成现实裁判或法律意见。</p>
      </aside>
    </main>
  )
}

function Courtroom({ role, onExit }: { role: UserRole; onExit: () => void }) {
  return (
    <main className="courtroom-shell">
      <header className="courtroom-header">
        <button className="brand brand-button" onClick={onExit}>
          <Scale aria-hidden="true" size={21} />
          <span>MootCourt Lab</span>
        </button>
        <div className="phase-status">
          <div><span>当前阶段</span><strong>开庭说明</strong></div>
          <div><span>你的席位</span><strong>{roleLabels[role]}</strong></div>
          <div><span>剩余回合</span><strong>40</strong></div>
          <div><span>预算</span><strong>¥20.00</strong></div>
        </div>
      </header>

      <section className="workspace">
        <aside className="workspace-panel case-files">
          <div className="panel-title"><BookOpen size={18} /><h2>可阅案卷</h2></div>
          <nav aria-label="案卷目录">
            <button className="file-item active"><FileText size={17} /><span>案件摘要</span><small>公开</small></button>
            <button className="file-item"><FileText size={17} /><span>证据目录</span><small>11 项</small></button>
            <button className="file-item"><Users size={17} /><span>证人名册</span><small>3 人</small></button>
            <button className="file-item"><ShieldCheck size={17} /><span>{roleLabels[role]}材料</span><small>私有</small></button>
          </nav>
        </aside>

        <section className="transcript" aria-labelledby="transcript-title">
          <div className="panel-title">
            <Gavel size={18} />
            <h2 id="transcript-title">公开庭审记录</h2>
            <span className="live-status">记录中</span>
          </div>
          <div className="transcript-body">
            <div className="record-entry system-entry">
              <div className="speaker">庭审控制器 <time>00:00</time></div>
              <p>现在进入开庭说明阶段。系统将按预定程序推进，并仅开放当前阶段的合法操作。</p>
            </div>
            <div className="empty-record">
              <Scale size={28} />
              <p>等待庭审开始</p>
            </div>
          </div>
        </section>

        <aside className="workspace-panel issue-panel">
          <div className="panel-title"><Scale size={18} /><h2>案件状态</h2></div>
          <dl className="status-list">
            <div><dt>争议事实</dt><dd>3</dd></div>
            <div><dt>已提交证据</dt><dd>0 / 11</dd></div>
            <div><dt>在庭证人</dt><dd>无</dd></div>
            <div><dt>程序请求</dt><dd>0</dd></div>
          </dl>
          <div className="boundary-note">
            <ShieldCheck size={17} />
            <p>角色材料已隔离。未授权内容不会进入模型上下文。</p>
          </div>
        </aside>
      </section>

      <footer className="action-dock">
        <div>
          <span className="action-label">当前合法操作</span>
          <p>开庭说明由确定性控制器执行。</p>
        </div>
        <button className="primary-action compact">继续 <ArrowRight size={18} /></button>
      </footer>
    </main>
  )
}

export default function App() {
  const [activeRole, setActiveRole] = useState<UserRole | null>(null)

  return activeRole ? (
    <Courtroom role={activeRole} onExit={() => setActiveRole(null)} />
  ) : (
    <CaseLobby onStart={setActiveRole} />
  )
}

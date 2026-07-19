import { ArrowLeft, BookOpenCheck, ExternalLink, Scale, ShieldAlert } from 'lucide-react'

import type { CourtReview } from '../types'

const statusLabels: Record<string, string> = {
  SUPPORTED: '已支持', DISPUTED: '有争议', INSUFFICIENT: '证据不足',
  SATISFIED: '满足', NOT_SATISFIED: '未满足', NOT_APPLICABLE: '不适用',
}

export function ReviewPage({ review, onBack }: { review: CourtReview; onBack: () => void }) {
  return (
    <main className="review-shell">
      <header className="review-header">
        <button className="icon-text-button" onClick={onBack}><ArrowLeft size={18} />返回庭审</button>
        <div className="brand"><Scale size={20} /><span>MootCourt Lab</span></div>
        <span className="environment-tag">教学复盘</span>
      </header>

      <section className="review-intro">
        <p className="eyebrow">结构化教学复盘</p>
        <h1>证据、事实与法律适用</h1>
        <div className="review-meta">
          <span>{review.jurisdiction}</span><span>法律基准日 {review.law_as_of_date}</span>
        </div>
        <div className="review-rule-grid">
          <div><strong>举证责任</strong><p>{review.burden_of_proof}</p></div>
          <div><strong>证明标准</strong><p>{review.standard_of_proof}</p></div>
        </div>
      </section>

      <nav className="review-jump" aria-label="复盘章节">
        <a href="#facts">事实判断</a><a href="#elements">构成要件</a><a href="#boundary">能力边界</a>
      </nav>

      <section id="facts" className="review-section">
        <div className="section-heading"><BookOpenCheck size={21} /><div><p className="eyebrow">庭审材料</p><h2>逐项事实判断</h2></div></div>
        <div className="finding-list">
          {review.fact_findings.map((item) => (
            <article className="finding-row" key={item.fact_id}>
              <div className="finding-id">{item.fact_id}</div>
              <div><h3>{item.description}</h3><p>支持证据：{item.submitted_supporting_evidence_ids.join('、') || '无'} · 已出现陈述：{item.appeared_statement_ids.join('、') || '无'}</p></div>
              <span className={`status-badge ${item.status.toLowerCase()}`}>{statusLabels[item.status]}</span>
            </article>
          ))}
        </div>
      </section>

      <section id="elements" className="review-section">
        <div className="section-heading"><Scale size={21} /><div><p className="eyebrow">法律适用</p><h2>构成要件检查</h2></div></div>
        <div className="element-list">
          {review.element_findings.map((item) => (
            <article className="element-row" key={item.element_id}>
              <header><span>{item.element_id}</span><h3>{item.description}</h3><span className={`status-badge ${item.status.toLowerCase()}`}>{statusLabels[item.status]}</span></header>
              <div className="element-facts">支持事实 {item.supporting_fact_ids.join('、') || '无'} · 反驳事实 {item.contradicting_fact_ids.join('、') || '无'}</div>
              <div className="citation-list">
                {item.citations.map((citation) => (
                  <details key={`${item.element_id}-${citation.source_id}`}>
                    <summary>{citation.instrument_title} · {citation.article_number}</summary>
                    <blockquote>{citation.text}</blockquote>
                    {citation.official_source_url && <a href={citation.official_source_url} target="_blank" rel="noreferrer">核验官方来源 <ExternalLink size={14} /></a>}
                  </details>
                ))}
              </div>
            </article>
          ))}
        </div>
      </section>

      <section id="boundary" className="review-boundary">
        <ShieldAlert size={22} />
        <div><h2>本报告不输出现实裁判结论</h2><p>{review.disclaimer}</p><p>未解决争点：{review.unresolved_issue_ids.join('、') || '无'}</p></div>
      </section>
    </main>
  )
}

import { useEffect, useState } from 'react'
import { ArrowLeft, BookOpenCheck, CheckCircle2, CircleAlert, ExternalLink, LoaderCircle, LocateFixed, Scale, ShieldAlert, Sparkles, Target, XCircle } from 'lucide-react'

import { api, ApiError } from '../api'
import { actionLabels, phaseLabels, roleLabels } from '../config'
import type { CourtReview, TurnQualityEvaluationReport } from '../types'
import { AccountControls } from './AccountControls'
import { useAccount } from '../auth-context'

const statusLabels: Record<string, string> = {
  SUPPORTED: '已支持', DISPUTED: '有争议', INSUFFICIENT: '证据不足',
  SATISFIED: '满足', NOT_SATISFIED: '未满足', NOT_APPLICABLE: '不适用',
}

const priorityLabels: Record<string, string> = { high: '优先处理', medium: '建议改进', low: '表现良好' }

export function ReviewPage({ review, onBack }: { review: CourtReview; onBack: (eventSequence?: number) => void }) {
  const account = useAccount()
  const hasLearningScore = review.score_dimensions.length > 0
  const [quality, setQuality] = useState<TurnQualityEvaluationReport | null>(null)
  const [qualityBusy, setQualityBusy] = useState(false)
  const [qualityError, setQualityError] = useState<string | null>(null)

  useEffect(() => {
    void api.getTurnEvaluation(review.session_id).then(setQuality).catch((caught) => {
      if (!(caught instanceof ApiError) || caught.status !== 404) setQualityError('读取深度点评失败。')
    })
  }, [review.session_id])

  const generateQuality = async () => {
    setQualityBusy(true); setQualityError(null)
    try { setQuality(await api.createTurnEvaluation(review.session_id)) }
    catch { setQualityError('Qwen 深度点评生成失败，请检查模型服务。') }
    finally { setQualityBusy(false) }
  }

  return (
    <main className="review-shell">
      <header className="review-header">
        <button className="icon-text-button" onClick={() => onBack()}><ArrowLeft size={18} />返回庭审</button>
        <div className="brand"><Scale size={20} /><span>MootCourt Lab</span></div>
        <div className="review-header-actions">
          <span className="environment-tag">教学复盘</span>
          {account && <AccountControls email={account.email} onSignOut={account.onSignOut} />}
        </div>
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
        <a href="#scoring">教学评分</a><a href="#turns">发言诊断</a><a href="#facts">事实判断</a><a href="#elements">构成要件</a><a href="#boundary">能力边界</a>
      </nav>

      <section id="scoring" className="review-section review-scoring">
        <div className="section-heading"><Target size={21} /><div><p className="eyebrow">教学反馈</p><h2>本席位庭审评分</h2></div></div>
        <div className="score-overview">
          <div className="score-total"><span>综合评分</span><strong>{hasLearningScore ? review.total_score : '--'}</strong><small>/ 100</small></div>
          <p>{hasLearningScore
            ? '评分依据已提交的优先证据、对方证据回应、必要法源覆盖及争点闭合情况生成。'
            : '该复盘生成于教学评分功能启用前，暂无可追溯评分。'}</p>
        </div>
        <div className="score-dimension-grid">
          {review.score_dimensions.map((item) => (
            <article className="score-dimension" key={item.key}>
              <div><h3>{item.label}</h3><strong>{item.score}<small> / 100</small></strong></div>
              <div className="score-track" aria-label={`${item.label} ${item.score} 分`}><span style={{ width: `${item.score}%` }} /></div>
              <p>{item.summary}</p>
            </article>
          ))}
        </div>
        {review.recommendations.length > 0 && <div className="recommendation-list" aria-label="改进建议">
          {review.recommendations.map((item) => (
            <article className={`recommendation ${item.priority}`} key={item.id}>
              <CircleAlert size={18} /><div><span>{priorityLabels[item.priority]}</span><h3>{item.title}</h3><p>{item.detail}</p></div>
            </article>
          ))}
        </div>}
      </section>

      <section id="turns" className="review-section turn-review-section">
        <div className="section-heading"><LocateFixed size={21} /><div><p className="eyebrow">过程诊断</p><h2>逐发言检查</h2></div></div>
        {review.turn_diagnostics.length === 0
          ? <p className="empty-turn-review">本庭没有可进行结构化诊断的用户发言。</p>
          : <div className="turn-diagnostic-list">{review.turn_diagnostics.map((item) => (
            <article className="turn-diagnostic" key={item.event_sequence_number}>
              <header>
                <div><span>#{item.event_sequence_number} · {roleLabels[item.actor_role as keyof typeof roleLabels] ?? item.actor_role}</span><h3>{actionLabels[item.action as keyof typeof actionLabels] ?? item.action}</h3><small>{phaseLabels[item.phase]}</small></div>
                <strong>{item.score}<small> / 100</small></strong>
              </header>
              <div className="turn-check-list">{item.checks.map((check) => (
                <div className={check.passed ? 'passed' : 'failed'} key={check.key}>
                  {check.passed ? <CheckCircle2 size={16} /> : <XCircle size={16} />}
                  <div><strong>{check.label}</strong><p>{check.detail}</p></div>
                </div>
              ))}</div>
              {item.recommendation && <p className="turn-recommendation">{item.recommendation}</p>}
              <button className="locate-event-button" onClick={() => onBack(item.event_sequence_number)}><LocateFixed size={15} />查看庭审记录 #{item.event_sequence_number}</button>
            </article>
          ))}</div>}
        {review.turn_diagnostics.length > 0 && !quality && <button className="quality-evaluation-button" disabled={qualityBusy} onClick={() => void generateQuality()}>{qualityBusy ? <LoaderCircle className="spin" size={17} /> : <Sparkles size={17} />}生成 Qwen 深度点评</button>}
        {qualityError && <p className="quality-error" role="alert">{qualityError}</p>}
        {quality && <div className="quality-evaluation"><header><Sparkles size={18} /><strong>Qwen 深度点评</strong><small>{quality.model}</small></header>{quality.evaluations.map((item) => <article key={item.event_sequence_number}><h3>庭审记录 #{item.event_sequence_number}</h3><p>表达组织 {item.organization_score} · 回应质量 {item.responsiveness_score} · 攻防策略 {item.advocacy_score}</p>{item.rewritten_example ? <blockquote>{item.rewritten_example}</blockquote> : <p className="rewrite-disabled">未同时关联证据和事实，已禁用模型改写。</p>}</article>)}</div>}
      </section>

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

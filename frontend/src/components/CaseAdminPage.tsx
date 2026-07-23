import { useEffect, useState } from 'react'
import { ArrowLeft, CheckCircle2, FileArchive, RefreshCw, ShieldAlert, Trash2, Upload, UserPlus } from 'lucide-react'

import { api, ApiError } from '../api'
import type {
  CaseImportAttempt,
  ManagedCasePackage,
  ManagedOrganization,
  OrganizationMemberRole,
  OrganizationMembers,
} from '../types'

interface Props {
  organizations: ManagedOrganization[]
  onBack: () => void
  onPublished: () => void
}

export function CaseAdminPage({ organizations, onBack, onPublished }: Props) {
  const [packages, setPackages] = useState<ManagedCasePackage[]>([])
  const [file, setFile] = useState<File | null>(null)
  const [report, setReport] = useState<CaseImportAttempt | null>(null)
  const [selectedOrganizationIds, setSelectedOrganizationIds] = useState<string[]>(
    organizations.map((item) => item.id),
  )
  const [busy, setBusy] = useState(false)
  const [selectedOrganizationId, setSelectedOrganizationId] = useState(organizations[0]?.id ?? '')
  const [members, setMembers] = useState<OrganizationMembers | null>(null)
  const [selectedUserId, setSelectedUserId] = useState<number | ''>('')
  const [selectedRole, setSelectedRole] = useState<OrganizationMemberRole>('learner')
  const [memberBusy, setMemberBusy] = useState(false)
  const [error, setError] = useState<string | null>(null)
  const draftCount = packages.filter((item) => item.lifecycle_status === 'draft').length
  const publishedCount = packages.filter((item) => item.lifecycle_status === 'published').length

  const load = async () => {
    setError(null)
    try {
      setPackages(await api.listManagedCases())
    } catch (caught) {
      setError(caught instanceof ApiError ? `${caught.code}: ${caught.message}` : '无法读取案件管理列表')
    }
  }

  useEffect(() => {
    let active = true
    void api.listManagedCases().then((items) => {
      if (active) setPackages(items)
    }).catch((caught: unknown) => {
      if (active) {
        setError(caught instanceof ApiError ? `${caught.code}: ${caught.message}` : '无法读取案件管理列表')
      }
    })
    return () => { active = false }
  }, [])

  useEffect(() => {
    if (!selectedOrganizationId) return
    let active = true
    void api.listOrganizationMembers(selectedOrganizationId).then((items) => {
      if (active) setMembers(items)
    }).catch((caught: unknown) => {
      if (active) setError(caught instanceof ApiError ? `${caught.code}: ${caught.message}` : '无法读取组织成员')
    })
    return () => { active = false }
  }, [selectedOrganizationId])

  const upload = async () => {
    if (!file) return
    setBusy(true)
    setError(null)
    try {
      const nextReport = await api.uploadCaseArchive(file)
      setReport(nextReport)
      if (nextReport.status !== 'rejected') await load()
    } catch (caught) {
      setError(caught instanceof ApiError ? `${caught.code}: ${caught.message}` : '案件上传失败')
    } finally {
      setBusy(false)
    }
  }

  const publish = async (item: ManagedCasePackage) => {
    setBusy(true)
    setError(null)
    try {
      await api.publishManagedCase(item.database_id, selectedOrganizationIds)
      await load()
      onPublished()
    } catch (caught) {
      setError(caught instanceof ApiError ? `${caught.code}: ${caught.message}` : '案件发布失败')
    } finally {
      setBusy(false)
    }
  }

  const saveMember = async (userId: number, role: OrganizationMemberRole) => {
    setMemberBusy(true)
    setError(null)
    try {
      setMembers(await api.setOrganizationMember(selectedOrganizationId, userId, role))
      setSelectedUserId('')
    } catch (caught) {
      setError(caught instanceof ApiError ? `${caught.code}: ${caught.message}` : '保存组织权限失败')
    } finally {
      setMemberBusy(false)
    }
  }

  const removeMember = async (userId: number) => {
    setMemberBusy(true)
    setError(null)
    try {
      setMembers(await api.removeOrganizationMember(selectedOrganizationId, userId))
    } catch (caught) {
      setError(caught instanceof ApiError ? `${caught.code}: ${caught.message}` : '移除组织成员失败')
    } finally {
      setMemberBusy(false)
    }
  }

  return (
    <main className="case-admin-shell">
      <header className="case-admin-header">
        <button className="admin-back-button" onClick={onBack}><ArrowLeft size={17} />返回庭审大厅</button>
        <div className="admin-title-lockup"><p className="eyebrow">CASE OPERATIONS / ADMIN</p><h1>案件管理</h1><p>导入案卷、发布训练版本、维护组织成员权限</p></div>
        <button className="admin-refresh-button" onClick={() => void load()}><RefreshCw size={16} />刷新数据</button>
      </header>

      <section className="admin-overview" aria-label="管理概览">
        <div className="admin-overview-intro"><p className="eyebrow">WORKSPACE OVERVIEW</p><h2>组织训练内容</h2><p>所有案件先进入草稿，明确发布范围后才会对学习者开放。</p></div>
        <div className="admin-metric"><span>案件版本</span><strong>{packages.length}</strong><small>全部版本</small></div>
        <div className="admin-metric"><span>待发布</span><strong className={draftCount > 0 ? 'accent' : ''}>{draftCount}</strong><small>需要处理</small></div>
        <div className="admin-metric"><span>已发布</span><strong>{publishedCount}</strong><small>{organizations.length} 个可管理组织</small></div>
      </section>

      <section className="case-admin-grid">
        <article className="case-import-card">
          <div className="tool-kicker"><FileArchive size={17} /><span>CONTENT INGESTION</span></div>
          <h2>导入新案卷</h2>
          <p>系统会校验目录穿越、压缩炸弹、文件清单、案卷 Schema、证据引用和角色材料边界。通过后先进入草稿，不会立即向学习者开放。</p>
          <label className="case-file-picker">
            <span>{file?.name ?? '选择 .zip 案件包'}</span>
            <input type="file" accept=".zip,application/zip" onChange={(event) => setFile(event.target.files?.[0] ?? null)} />
          </label>
          <button className="primary-action compact" disabled={!file || busy} onClick={() => void upload()}>
            <Upload size={16} />{busy ? '正在校验' : '上传并校验'}
          </button>
          {report && (
            <div className={`import-report ${report.status}`} role="status">
              {report.status === 'rejected' ? <ShieldAlert size={18} /> : <CheckCircle2 size={18} />}
              <div>
                <strong>{report.status === 'rejected' ? '校验未通过' : report.status === 'duplicate' ? '版本已存在' : '已创建草稿'}</strong>
                {report.errors.map((issue) => <p key={`${issue.code}-${issue.path}`}>{issue.code}{issue.path ? ` · ${issue.path}` : ''}：{issue.message}</p>)}
              </div>
            </div>
          )}
          <fieldset className="organization-picker">
            <legend>发布范围</legend>
            {organizations.map((organization) => (
              <label key={organization.id}>
                <input
                  type="checkbox"
                  checked={selectedOrganizationIds.includes(organization.id)}
                  onChange={(event) => setSelectedOrganizationIds((current) => (
                    event.target.checked
                      ? [...current, organization.id]
                      : current.filter((id) => id !== organization.id)
                  ))}
                />
                <span>{organization.name}<small>{organization.slug}</small></span>
              </label>
            ))}
          </fieldset>
          {error && <p className="page-error" role="alert">{error}</p>}
        </article>

        <section className="managed-case-list" aria-labelledby="managed-cases-title">
          <div className="managed-case-heading"><div><p className="eyebrow">CONTENT LIBRARY</p><h2 id="managed-cases-title">案件版本</h2></div><span>{packages.length} 个版本</span></div>
          {packages.map((item) => (
            <article className="managed-case-row" key={item.database_id}>
              <div><strong>{item.title}</strong><p>{item.case_id} · {item.package_version}</p></div>
              <span className={`lifecycle-badge ${item.lifecycle_status}`}>{item.lifecycle_status === 'draft' ? '草稿' : '已发布'}</span>
              <small>{item.law_as_of_date}<br />{item.source_filename ?? '系统导入'}</small>
              {item.lifecycle_status === 'draft' ? (
                <button className="secondary-action" disabled={busy || selectedOrganizationIds.length === 0} onClick={() => void publish(item)}>发布到 {selectedOrganizationIds.length} 个组织</button>
              ) : <span className="published-scope">已授权 {item.organization_ids.length} 个组织</span>}
            </article>
          ))}
          {packages.length === 0 && <p className="managed-case-empty">暂无可管理的案件版本。</p>}
        </section>
      </section>

      <section className="organization-admin-panel" aria-labelledby="organization-admin-title">
        <div className="managed-case-heading">
          <div><p className="eyebrow">ACCESS CONTROL</p><h2 id="organization-admin-title">组织权限</h2><p className="panel-subtitle">谁可以进入本组织，以及他们能做什么</p></div>
          <select value={selectedOrganizationId} onChange={(event) => setSelectedOrganizationId(event.target.value)}>
            {organizations.map((organization) => <option key={organization.id} value={organization.id}>{organization.name}</option>)}
          </select>
        </div>
        <p className="admin-help">只显示已经登录过的用户。修改角色会立即影响案件可见范围和庭审管理权限。</p>
        {members && (
          <>
            <div className="member-add-row">
              <select aria-label="选择用户" value={selectedUserId} onChange={(event) => setSelectedUserId(event.target.value ? Number(event.target.value) : '')}>
                <option value="">选择已登录用户</option>
                {members.available_users.map((user) => <option key={user.user_id} value={user.user_id}>{user.email ?? `用户 ${user.user_id}`}</option>)}
              </select>
              <select aria-label="新成员角色" value={selectedRole} onChange={(event) => setSelectedRole(event.target.value as OrganizationMemberRole)}>
                <option value="learner">学习者</option>
                <option value="instructor">指导教师</option>
                <option value="admin">管理员</option>
              </select>
              <button className="secondary-action" disabled={selectedUserId === '' || memberBusy} onClick={() => void saveMember(selectedUserId as number, selectedRole)}>
                <UserPlus size={16} />添加成员
              </button>
            </div>
            <div className="member-list">
              {members.members.map((member) => (
                <div className="member-row" key={member.user_id}>
                  <div><strong>{member.display_name ?? member.email ?? `用户 ${member.user_id}`}</strong><small>{member.email ?? '未提供邮箱'}</small></div>
                  <select aria-label={`${member.email ?? member.user_id}角色`} value={member.role} disabled={memberBusy} onChange={(event) => void saveMember(member.user_id, event.target.value as OrganizationMemberRole)}>
                    <option value="learner">学习者</option><option value="instructor">指导教师</option><option value="admin">管理员</option>
                  </select>
                  <button className="icon-action" title="移除成员" aria-label={`移除 ${member.email ?? member.user_id}`} disabled={memberBusy} onClick={() => void removeMember(member.user_id)}><Trash2 size={16} /></button>
                </div>
              ))}
            </div>
          </>
        )}
      </section>
    </main>
  )
}

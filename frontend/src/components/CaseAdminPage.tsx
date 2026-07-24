import { useEffect, useMemo, useState } from 'react'
import {
  ArrowLeft,
  Calendar,
  CheckCircle2,
  ChevronDown,
  ChevronUp,
  FileArchive,
  Globe,
  LoaderCircle,
  RefreshCw,
  Search,
  ShieldAlert,
  Trash2,
  Upload,
  UserPlus,
  X,
} from 'lucide-react'

import { api, ApiError } from '../api'
import type {
  CaseImportAttempt,
  ManagedCasePackage,
  ManagedOrganization,
  OrganizationMemberRole,
  OrganizationMembers,
} from '../types'
import { AccountControls } from './AccountControls'
import { useAccount } from '../auth-context'
import { WorkspaceSidebar, type WorkspaceSection } from './WorkspaceSidebar'

interface Props {
  organizations: ManagedOrganization[]
  section: Exclude<WorkspaceSection, 'training'>
  onNavigate: (section: WorkspaceSection) => void
  onPublished: () => void
  embedded?: boolean
}

export function CaseAdminPage({ organizations, section, onNavigate, onPublished, embedded = true }: Props) {
  const account = useAccount()
  const [packages, setPackages] = useState<ManagedCasePackage[]>([])
  const [file, setFile] = useState<File | null>(null)
  const [report, setReport] = useState<CaseImportAttempt | null>(null)
  const [busy, setBusy] = useState(false)
  const [selectedOrganizationId, setSelectedOrganizationId] = useState(organizations[0]?.id ?? '')
  const [members, setMembers] = useState<OrganizationMembers | null>(null)
  const [membersOrgId, setMembersOrgId] = useState<string | null>(null)
  const [selectedUserId, setSelectedUserId] = useState<number | ''>('')
  const [selectedRole, setSelectedRole] = useState<OrganizationMemberRole>('learner')
  const [memberBusy, setMemberBusy] = useState(false)
  const [error, setError] = useState<string | null>(null)
  const [packagesLoaded, setPackagesLoaded] = useState(false)
  const [expandedIds, setExpandedIds] = useState<Set<number>>(new Set())
  const [searchQuery, setSearchQuery] = useState('')
  const [statusFilter, setStatusFilter] = useState<'all' | 'draft' | 'published'>('all')
  const [packageOrgMap, setPackageOrgMap] = useState<Record<number, string[]>>({})
  const [toast, setToast] = useState<{ type: 'success' | 'error'; message: string } | null>(null)

  const filteredPackages = useMemo(() => {
    const query = searchQuery.trim().toLowerCase()
    return packages.filter((item) => {
      if (statusFilter !== 'all' && item.lifecycle_status !== statusFilter) return false
      if (!query) return true
      return item.title.toLowerCase().includes(query)
        || item.case_id.toLowerCase().includes(query)
        || item.package_version.toLowerCase().includes(query)
    })
  }, [packages, searchQuery, statusFilter])

  const draftCount = packages.filter((item) => item.lifecycle_status === 'draft').length
  const publishedCount = packages.filter((item) => item.lifecycle_status === 'published').length
  // 成员数据按组织来源标记，切换组织后旧数据自然失效，无需在 effect 中重置状态。
  const membersLoading = membersOrgId !== selectedOrganizationId

  useEffect(() => {
    if (!toast) return undefined
    const timer = window.setTimeout(() => setToast(null), 3000)
    return () => window.clearTimeout(timer)
  }, [toast])

  const showToast = (message: string, type: 'success' | 'error' = 'success') => {
    setToast({ message, type })
  }

  const toggleExpanded = (databaseId: number) => {
    setExpandedIds((current) => {
      const next = new Set(current)
      if (next.has(databaseId)) next.delete(databaseId)
      else next.add(databaseId)
      return next
    })
  }

  const deriveDefaultOrgIds = (item: ManagedCasePackage) =>
    item.organization_ids.length > 0 ? item.organization_ids : organizations.map((organization) => organization.id)

  const load = async () => {
    setError(null)
    try {
      const items = await api.listManagedCases()
      setPackages(items)
      setPackageOrgMap(Object.fromEntries(items.map((item) => [item.database_id, deriveDefaultOrgIds(item)])))
    } catch (caught) {
      setError(caught instanceof ApiError ? `${caught.code}: ${caught.message}` : '无法读取案件管理列表')
    }
  }

  useEffect(() => {
    let active = true
    void api.listManagedCases().then((items) => {
      if (active) {
        setPackages(items)
        setPackageOrgMap(Object.fromEntries(items.map((item) => [item.database_id, deriveDefaultOrgIds(item)])))
        setPackagesLoaded(true)
      }
    }).catch((caught: unknown) => {
      if (active) {
        setError(caught instanceof ApiError ? `${caught.code}: ${caught.message}` : '无法读取案件管理列表')
        setPackagesLoaded(true)
      }
    })
    return () => { active = false }
  }, [])

  useEffect(() => {
    if (!selectedOrganizationId) return
    let active = true
    void api.listOrganizationMembers(selectedOrganizationId).then((items) => {
      if (active) {
        setMembers(items)
        setMembersOrgId(selectedOrganizationId)
      }
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
      if (nextReport.status !== 'rejected') {
        setFile(null)
        await load()
        showToast('案卷导入成功，已进入草稿。')
      }
    } catch (caught) {
      setError(caught instanceof ApiError ? `${caught.code}: ${caught.message}` : '案件上传失败')
    } finally {
      setBusy(false)
    }
  }

  const publish = async (item: ManagedCasePackage, organizationIds: string[]) => {
    if (organizationIds.length === 0) {
      setError('请至少选择一个发布组织。')
      return
    }
    setBusy(true)
    setError(null)
    try {
      await api.publishManagedCase(item.database_id, organizationIds)
      await load()
      onPublished()
      showToast(`《${item.title}》已发布到 ${organizationIds.length} 个组织。`)
    } catch (caught) {
      setError(caught instanceof ApiError ? `${caught.code}: ${caught.message}` : '案件发布失败')
    } finally {
      setBusy(false)
    }
  }

  const updateAccess = async (item: ManagedCasePackage, organizationIds: string[]) => {
    if (organizationIds.length === 0) {
      setError('请至少保留一个授权组织。')
      return
    }
    setBusy(true)
    setError(null)
    try {
      await api.updateManagedCaseAccess(item.database_id, organizationIds)
      await load()
      onPublished()
      showToast(`《${item.title}》授权范围已更新。`)
    } catch (caught) {
      setError(caught instanceof ApiError ? `${caught.code}: ${caught.message}` : '更新授权范围失败')
    } finally {
      setBusy(false)
    }
  }

  const deleteCase = async (item: ManagedCasePackage) => {
    setBusy(true)
    setError(null)
    try {
      await api.deleteManagedCase(item.database_id)
      await load()
      showToast(`《${item.title}》已删除。`)
    } catch (caught) {
      setError(caught instanceof ApiError ? `${caught.code}: ${caught.message}` : '删除案件失败')
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
      showToast('成员权限已更新。')
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
      showToast('成员已移除。')
    } catch (caught) {
      setError(caught instanceof ApiError ? `${caught.code}: ${caught.message}` : '移除组织成员失败')
    } finally {
      setMemberBusy(false)
    }
  }

  return (
    <main className={embedded ? 'case-admin-shell embedded-admin-content' : 'case-admin-shell'}>
      {toast && <div className={`toast ${toast.type}`} role="status"><span>{toast.message}</span><button type="button" aria-label="关闭" onClick={() => setToast(null)}><X size={14} /></button></div>}
      {!embedded && <header className="case-admin-header">
        <button className="admin-back-button" onClick={() => onNavigate('training')}><ArrowLeft size={17} />返回案件训练</button>
        <div className="admin-title-lockup"><p className="eyebrow">WORKSPACE / ADMIN</p><h1>{section === 'organization-access' ? '组织与权限' : '案件管理'}</h1><p>{section === 'organization-access' ? '管理组织成员角色与案件访问范围' : '导入案卷、发布训练版本'}</p></div>
        <div className="admin-header-actions">
          <button className="admin-refresh-button" onClick={() => void load()}><RefreshCw size={16} />刷新数据</button>
          {account && <AccountControls email={account.email} onSignOut={account.onSignOut} />}
        </div>
      </header>}

      <div className="workspace-admin-body">
        {!embedded && <WorkspaceSidebar activeSection={section} canManageCases onNavigate={onNavigate} />}
        <div className="case-admin-main">
        {embedded && <div className="embedded-admin-heading"><p className="eyebrow">WORKSPACE / ADMIN</p><h1>{section === 'organization-access' ? '组织与权限' : '案件管理'}</h1></div>}
        {section === 'case-management' && <>
      <section className="admin-overview" aria-label="管理概览">
        {/* <div className="admin-overview-intro"><p className="eyebrow">WORKSPACE OVERVIEW</p><h2>组织训练内容</h2><p>所有案件先进入草稿，明确发布范围后才会对学习者开放。</p></div> */}
        <div className="admin-metric"><span>案件版本</span><strong>{packages.length}</strong><small>全部版本</small></div>
        <div className="admin-metric"><span>待发布</span><strong className={draftCount > 0 ? 'accent' : ''}>{draftCount}</strong><small>需要处理</small></div>
        <div className="admin-metric"><span>已发布</span><strong>{publishedCount}</strong><small>{organizations.length} 个可管理组织</small></div>
      </section>

      <section className="case-admin-grid">
        <article className="case-import-card">
          <div className="tool-kicker"><FileArchive size={17} /><span>CONTENT INGESTION</span></div>
          <h2>导入新案卷</h2>
          <p>系统会校验目录穿越、压缩炸弹、文件清单、案卷 Schema、证据引用和角色材料边界。</p>
          <label className="case-file-picker">
            <span>{file?.name ?? '选择 .zip 案件包'}</span>
            <input type="file" accept=".zip,application/zip" onChange={(event) => setFile(event.target.files?.[0] ?? null)} />
          </label>
          <button className="primary-action compact" disabled={!file || busy} onClick={() => void upload()}>
            {busy ? <LoaderCircle className="spin" size={16} /> : <Upload size={16} />}{busy ? '正在校验' : '上传并校验'}
          </button>
          {report && (
            <div className={`import-report ${report.status}`} role="status">
              {report.status === 'rejected' ? <ShieldAlert size={18} /> : <CheckCircle2 size={18} />}
              <div>
                <strong>{report.status === 'rejected' ? '校验未通过' : report.status === 'duplicate' ? '版本已存在' : '已创建草稿'}</strong>
                <div className="import-report-errors">
                  {report.errors.map((issue) => <p key={`${issue.code}-${issue.path}`}>{issue.code}{issue.path ? ` · ${issue.path}` : ''}：{issue.message}</p>)}
                </div>
              </div>
            </div>
          )}
          {error && <p className="page-error" role="alert">{error}</p>}
        </article>

        <section className="managed-case-list" aria-labelledby="managed-cases-title">
          <div className="managed-case-heading">
            <div><p className="eyebrow">CONTENT LIBRARY</p><h2 id="managed-cases-title">案件版本</h2></div>
            <span>{filteredPackages.length} / {packages.length} 个版本</span>
          </div>
          <div className="case-list-toolbar">
            <div className="case-search">
              <Search size={15} aria-hidden="true" />
              <input type="search" placeholder="搜索标题或案件号…" value={searchQuery} onChange={(event) => setSearchQuery(event.target.value)} />
            </div>
            <div className="case-filter" role="group" aria-label="状态过滤">
              {(['all', 'draft', 'published'] as const).map((value) => (
                <button key={value} className={statusFilter === value ? 'active' : ''} onClick={() => setStatusFilter(value)}>
                  {value === 'all' ? '全部' : value === 'draft' ? '草稿' : '已发布'}
                </button>
              ))}
            </div>
          </div>
          {filteredPackages.map((item) => {
            const expanded = expandedIds.has(item.database_id)
            // 草稿且从未授权时默认选中全部可管理组织，已发布或已有授权范围则保持原范围。
            const defaultIds = item.organization_ids.length > 0 ? item.organization_ids : organizations.map((organization) => organization.id)
            const selectedIds = packageOrgMap[item.database_id] ?? defaultIds
            return (
              <article className={`managed-case-row ${expanded ? 'expanded' : ''}`} key={item.database_id}>
                <div className="managed-case-summary">
                  <div className="managed-case-title">
                    <strong>{item.title}</strong>
                    <span className={`lifecycle-badge ${item.lifecycle_status}`}>{item.lifecycle_status === 'draft' ? '草稿' : '已发布'}</span>
                  </div>
                  <p className="managed-case-id">{item.case_id} · {item.package_version}</p>
                  <button className="case-expand-button" type="button" aria-expanded={expanded} onClick={() => toggleExpanded(item.database_id)}>
                    {expanded ? <ChevronUp size={16} /> : <ChevronDown size={16} />}<span>{expanded ? '收起' : '详情'}</span>
                  </button>
                </div>
                {expanded && (
                  <div className="managed-case-detail">
                    <div className="case-meta-grid">
                      <span><Globe size={14} aria-hidden="true" />{item.jurisdiction}</span>
                      <span><Calendar size={14} aria-hidden="true" />{item.law_as_of_date}</span>
                      <span>来源：{item.source_filename ?? '系统导入'}</span>
                      <span>导入：{new Date(item.created_at).toLocaleDateString('zh-CN')}</span>
                    </div>
                    <div className="package-publish-form">
                      <fieldset>
                        <legend>{item.lifecycle_status === 'draft' ? '发布到组织' : '授权组织'}</legend>
                        {organizations.map((organization) => (
                          <label key={organization.id}>
                            <input
                              type="checkbox"
                              checked={selectedIds.includes(organization.id)}
                              disabled={busy}
                              onChange={(event) => setPackageOrgMap((current) => ({
                                ...current,
                                [item.database_id]: event.target.checked
                                  ? [...selectedIds, organization.id]
                                  : selectedIds.filter((id) => id !== organization.id),
                              }))}
                            />
                            <span>{organization.name}</span>
                          </label>
                        ))}
                      </fieldset>
                      <div className="package-publish-actions">
                        {item.lifecycle_status === 'draft' ? (
                          <>
                            <button className="secondary-action" disabled={busy || selectedIds.length === 0} onClick={() => void publish(item, selectedIds)}>
                              发布到 {selectedIds.length} 个组织
                            </button>
                            <button className="icon-action danger" title="删除草稿" aria-label={`删除草稿 ${item.title}`} disabled={busy} onClick={() => { if (window.confirm(`确认删除草稿《${item.title}》？此操作不可恢复。`)) void deleteCase(item) }}>
                              <Trash2 size={16} />
                            </button>
                          </>
                        ) : (
                          <button className="secondary-action" disabled={busy || selectedIds.length === 0} onClick={() => void updateAccess(item, selectedIds)}>
                            更新授权范围
                          </button>
                        )}
                      </div>
                    </div>
                  </div>
                )}
              </article>
            )
          })}
          {!packagesLoaded && <p className="managed-case-empty">正在读取案件版本…</p>}
          {packagesLoaded && filteredPackages.length === 0 && (
            <p className="managed-case-empty">{packages.length === 0 ? '暂无可管理的案件版本。' : '没有匹配当前过滤条件的案件。'}</p>
          )}
        </section>
      </section>

        </>}

        {section === 'organization-access' && <section className="organization-admin-panel" aria-labelledby="organization-admin-title">
        <div className="managed-case-heading">
          <div><p className="eyebrow">ACCESS CONTROL</p><h2 id="organization-admin-title">组织权限</h2></div>
          <select value={selectedOrganizationId} onChange={(event) => { setSelectedOrganizationId(event.target.value); setError(null) }}>
            {organizations.map((organization) => <option key={organization.id} value={organization.id}>{organization.name}</option>)}
          </select>
        </div>
        {error && <p className="page-error" role="alert">{error}</p>}
        {membersLoading && !error && <p className="managed-case-empty">正在读取组织成员…</p>}
        {!membersLoading && members && (
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
                  <button className="icon-action" title="移除成员" aria-label={`移除 ${member.email ?? member.user_id}`} disabled={memberBusy} onClick={() => { if (window.confirm(`确认移除成员 ${member.email ?? member.user_id}？移除后其将失去本组织的案件访问权限。`)) void removeMember(member.user_id) }}><Trash2 size={16} /></button>
                </div>
              ))}
            </div>
          </>
        )}
      </section>}
        </div>
      </div>
    </main>
  )
}

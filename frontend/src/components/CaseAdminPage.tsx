import { useEffect, useState } from 'react'
import { ArrowLeft, CheckCircle2, FileArchive, RefreshCw, ShieldAlert, Upload } from 'lucide-react'

import { api, ApiError } from '../api'
import type { CaseImportAttempt, ManagedCasePackage, ManagedOrganization } from '../types'

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
  const [error, setError] = useState<string | null>(null)

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

  return (
    <main className="case-admin-shell">
      <header className="case-admin-header">
        <button className="ghost-action" onClick={onBack}><ArrowLeft size={17} />返回庭审大厅</button>
        <div><p className="eyebrow">CASE OPERATIONS</p><h1>案件导入与发布</h1></div>
        <button className="ghost-action" onClick={() => void load()}><RefreshCw size={16} />刷新</button>
      </header>

      <section className="case-admin-grid">
        <article className="case-import-card">
          <FileArchive size={28} />
          <h2>上传案件 ZIP</h2>
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
          <div className="managed-case-heading"><div><p className="eyebrow">VERSIONS</p><h2 id="managed-cases-title">案件版本</h2></div><span>{packages.length} 个版本</span></div>
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
    </main>
  )
}

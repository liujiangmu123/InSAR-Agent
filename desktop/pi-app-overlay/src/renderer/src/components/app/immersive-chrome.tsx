import { useTranslation } from 'react-i18next'
import { CircleDot, PanelLeft } from '@renderer/components/icons'
import { WindowControls } from '@renderer/components/app/window-controls'
import { cn } from '@renderer/lib/utils'
import { useUIStore } from '@renderer/stores/ui-store'
import { isMac, MAC_TRAFFIC_LIGHTS_SPACER_CLASS } from '@renderer/lib/platform'

/** 无边框窗口顶栏：可拖拽区域 + 侧栏开关（主对话页无厚重 TopBar；无运行绿点/状态条） */
export function ImmersiveChrome({
  projectName,
}: {
  projectName?: string
  /** @deprecated ignored — status strip removed for lower chrome noise */
  isRunning?: boolean
  /** @deprecated ignored */
  statusLabelKey?: string
}) {
  const { t } = useTranslation()
  const collapsed = useUIStore((s) => s.sidebarCollapsed)
  const toggleSidebar = useUIStore((s) => s.toggleSidebar)
  const rightPanelCatalog = useUIStore((s) => s.rightPanelCatalog)
  const showInsarWorkbench = rightPanelCatalog.some((c) => c.adapterId === 'insar')
  return (
    <div
      className="electron-drag relative z-20 flex h-9 shrink-0 items-center justify-between px-2"
      style={{ background: 'color-mix(in srgb, var(--surface-sidebar) 92%, transparent)' }}
    >
      <div className="electron-no-drag flex min-w-0 items-center gap-1">
        {isMac && <div aria-hidden className={MAC_TRAFFIC_LIGHTS_SPACER_CLASS} />}
        <button
          type="button"
          onClick={toggleSidebar}
          title={collapsed ? t('common:topbar.expandSidebar') : t('common:topbar.collapseSidebar')}
          className="chrome-icon-btn flex h-7 w-7 items-center justify-center rounded-md text-foreground-secondary"
        >
          <PanelLeft
            className={cn(
              'h-3.5 w-3.5 transition-transform duration-[var(--motion-normal)] ease-[var(--motion-ease)]',
              collapsed && 'rotate-180',
            )}
          />
        </button>
        <div className="flex min-w-0 items-center gap-1.5 px-1 text-[12px] text-foreground-secondary select-none">
          <CircleDot className="h-3.5 w-3.5 shrink-0 text-primary" />
          <span className="font-medium text-foreground/90">{t('common:app.name')}</span>
          {projectName && (
            <>
              <span className="opacity-35">/</span>
              <span className="max-w-[200px] truncate">{projectName}</span>
            </>
          )}
          {showInsarWorkbench && (
            <span className="rounded-full border border-border/60 px-2 py-0.5 text-[10px] tracking-wide text-muted-foreground">
              {t('common:topbar.insarWorkbench')}
            </span>
          )}
        </div>
      </div>
      <div className="electron-no-drag flex h-9 items-center gap-2">
        <WindowControls className="-mr-2 border-l border-border/40 pl-0.5" />
      </div>
    </div>
  )
}

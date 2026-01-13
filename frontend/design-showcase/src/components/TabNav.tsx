import { Play, Eye, UsersThree, Key, Globe, ShieldCheck } from '@phosphor-icons/react'
import { ReactNode, useRef, useState, useEffect } from 'react'

type TabId = 'campaign' | 'live' | 'sessions' | 'credentials' | 'proxies' | 'admin'

interface Tab {
  id: TabId
  label: string
  icon: ReactNode
}

interface TabNavProps {
  activeTab: TabId
  onTabChange: (tab: TabId) => void
}

const tabs: Tab[] = [
  { id: 'campaign', label: 'Campaign', icon: <Play weight="bold" className="w-4 h-4" /> },
  { id: 'live', label: 'Live View', icon: <Eye weight="bold" className="w-4 h-4" /> },
  { id: 'sessions', label: 'Sessions', icon: <UsersThree weight="bold" className="w-4 h-4" /> },
  { id: 'credentials', label: 'Credentials', icon: <Key weight="bold" className="w-4 h-4" /> },
  { id: 'proxies', label: 'Proxies', icon: <Globe weight="bold" className="w-4 h-4" /> },
  { id: 'admin', label: 'Admin', icon: <ShieldCheck weight="bold" className="w-4 h-4" /> },
]

export function TabNav({ activeTab, onTabChange }: TabNavProps) {
  const containerRef = useRef<HTMLDivElement>(null)
  const buttonRefs = useRef<(HTMLButtonElement | null)[]>([])
  const [indicatorStyle, setIndicatorStyle] = useState({ left: 4, width: 100, opacity: 0 })

  // Update indicator position when active tab changes
  useEffect(() => {
    const updateIndicator = () => {
      const container = containerRef.current
      if (!container) return

      const activeIndex = tabs.findIndex(t => t.id === activeTab)
      const activeButton = buttonRefs.current[activeIndex]

      if (activeButton) {
        setIndicatorStyle({
          left: activeButton.offsetLeft,
          width: activeButton.offsetWidth,
          opacity: 1,
        })
      }
    }

    // Run immediately and also after a small delay to handle initial render
    updateIndicator()
    const timer = setTimeout(updateIndicator, 50)
    return () => clearTimeout(timer)
  }, [activeTab])

  return (
    <nav className="px-6 py-2">
      <div
        ref={containerRef}
        className="relative flex gap-1 p-1"
        style={{
          background: 'var(--card)',
          border: '1px solid var(--border)',
          borderRadius: '9999px',
        }}
      >
        {/* Sliding indicator - the liquid pill */}
        <div
          className="absolute top-1 bottom-1"
          style={{
            left: indicatorStyle.left,
            width: indicatorStyle.width,
            opacity: indicatorStyle.opacity,
            background: 'var(--accent)',
            borderRadius: '9999px',
            transition: 'left 0.3s cubic-bezier(0.4, 0, 0.2, 1), width 0.3s cubic-bezier(0.4, 0, 0.2, 1), opacity 0.15s ease',
          }}
        />

        {/* Tab buttons */}
        {tabs.map((tab, index) => {
          const isActive = activeTab === tab.id

          return (
            <button
              key={tab.id}
              ref={(el) => { buttonRefs.current[index] = el }}
              onClick={() => onTabChange(tab.id)}
              className="relative z-10 flex items-center gap-2 px-4 py-2 font-medium text-sm"
              style={{
                borderRadius: '9999px',
                background: 'transparent',
                color: isActive ? '#fff' : 'var(--text-secondary)',
                transition: 'color 0.2s ease',
              }}
            >
              {tab.icon}
              <span>{tab.label}</span>
            </button>
          )
        })}
      </div>
    </nav>
  )
}

export type { TabId }

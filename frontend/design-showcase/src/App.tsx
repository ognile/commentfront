import { useState } from 'react'
import { Header } from './components/Header'
import { TabNav, type TabId } from './components/TabNav'
import { CampaignQueue } from './components/CampaignQueue'
import { LiveView } from './components/LiveView'
import { SessionCards } from './components/SessionCards'
import { PearlBackground, type PearlVariant } from './components/PearlBackground'
import { GradientSwitcher } from './components/GradientSwitcher'
import { GlassPanel } from './components/GlassPanel'
import { Key, GlobeHemisphereWest, ShieldCheck } from '@phosphor-icons/react'

function App() {
  const [activeTab, setActiveTab] = useState<TabId>('campaign')
  const [pearlVariant, setPearlVariant] = useState<PearlVariant>('warm')

  // Placeholder content for non-demo tabs
  const PlaceholderTab = ({ icon: Icon, title, description }: { icon: React.ElementType; title: string; description: string }) => (
    <GlassPanel className="p-8 text-center">
      <div
        className="w-16 h-16 mx-auto mb-5 flex items-center justify-center"
        style={{
          background: 'var(--accent-soft)',
          border: '1px solid var(--border)',
          borderRadius: '9999px',
        }}
      >
        <Icon weight="duotone" className="w-8 h-8" style={{ color: 'var(--accent)' }} />
      </div>
      <h2 className="font-display font-semibold text-xl text-primary mb-2">
        {title}
      </h2>
      <p className="text-sm text-secondary max-w-md mx-auto">
        {description}
      </p>
    </GlassPanel>
  )

  const renderTabContent = () => {
    switch (activeTab) {
      case 'campaign':
        return <CampaignQueue />
      case 'live':
        return <LiveView />
      case 'sessions':
        return <SessionCards />
      case 'credentials':
        return (
          <PlaceholderTab
            icon={Key}
            title="Credentials"
            description="Manage login credentials, 2FA secrets, and link sessions."
          />
        )
      case 'proxies':
        return (
          <PlaceholderTab
            icon={GlobeHemisphereWest}
            title="Proxies"
            description="Configure and test proxy servers. Monitor health status."
          />
        )
      case 'admin':
        return (
          <PlaceholderTab
            icon={ShieldCheck}
            title="Admin"
            description="Manage users, permissions, and system settings."
          />
        )
      default:
        return null
    }
  }

  return (
    <div className="min-h-screen relative">
      {/* Pearl gradient background */}
      <PearlBackground variant={pearlVariant} />

      {/* Content layer */}
      <div className="relative z-10">
        <Header />
        <TabNav activeTab={activeTab} onTabChange={setActiveTab} />

        <main className="max-w-6xl mx-auto px-6 py-6 pb-32">
          {renderTabContent()}
        </main>
      </div>

      {/* Gradient switcher */}
      <GradientSwitcher
        currentVariant={pearlVariant}
        onVariantChange={setPearlVariant}
      />
    </div>
  )
}

export default App

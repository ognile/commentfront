import * as React from "react"
import * as TabsPrimitive from "@radix-ui/react-tabs"

import { cn } from "@/lib/utils"

const Tabs = TabsPrimitive.Root

// Custom TabsList with liquid sliding indicator
const TabsList = React.forwardRef<
  React.ElementRef<typeof TabsPrimitive.List>,
  React.ComponentPropsWithoutRef<typeof TabsPrimitive.List>
>(({ className, children, ...props }, ref) => {
  const containerRef = React.useRef<HTMLDivElement>(null)
  const [indicatorStyle, setIndicatorStyle] = React.useState({ left: 4, width: 100, opacity: 0 })

  // Update indicator on mount and when children change
  React.useEffect(() => {
    const updateIndicator = () => {
      const container = containerRef.current
      if (!container) return

      const activeButton = container.querySelector('[data-state="active"]') as HTMLElement
      if (activeButton) {
        setIndicatorStyle({
          left: activeButton.offsetLeft,
          width: activeButton.offsetWidth,
          opacity: 1,
        })
      }
    }

    // Run immediately and after a small delay
    updateIndicator()
    const timer = setTimeout(updateIndicator, 50)

    // Set up MutationObserver to watch for data-state changes
    const container = containerRef.current
    if (container) {
      const observer = new MutationObserver(updateIndicator)
      observer.observe(container, {
        attributes: true,
        subtree: true,
        attributeFilter: ['data-state']
      })
      return () => {
        clearTimeout(timer)
        observer.disconnect()
      }
    }

    return () => clearTimeout(timer)
  }, [children])

  return (
    <TabsPrimitive.List
      ref={ref}
      className={cn(
        "inline-flex items-center justify-center rounded-full bg-white border border-[rgba(0,0,0,0.1)] p-1 text-[#666666] relative",
        className
      )}
      {...props}
    >
      <div ref={containerRef} className="relative flex gap-1">
        {/* Sliding indicator */}
        <div
          className="absolute top-0 bottom-0"
          style={{
            left: indicatorStyle.left,
            width: indicatorStyle.width,
            opacity: indicatorStyle.opacity,
            background: '#333333',
            borderRadius: '9999px',
            transition: 'left 0.3s cubic-bezier(0.4, 0, 0.2, 1), width 0.3s cubic-bezier(0.4, 0, 0.2, 1), opacity 0.15s ease',
          }}
        />
        {children}
      </div>
    </TabsPrimitive.List>
  )
})
TabsList.displayName = TabsPrimitive.List.displayName

const TabsTrigger = React.forwardRef<
  React.ElementRef<typeof TabsPrimitive.Trigger>,
  React.ComponentPropsWithoutRef<typeof TabsPrimitive.Trigger>
>(({ className, ...props }, ref) => (
  <TabsPrimitive.Trigger
    ref={ref}
    className={cn(
      "relative z-10 inline-flex items-center justify-center whitespace-nowrap rounded-full px-4 py-2 text-sm font-medium transition-colors focus-visible:outline-none focus-visible:ring-2 focus-visible:ring-ring focus-visible:ring-offset-2 disabled:pointer-events-none disabled:opacity-50 data-[state=active]:text-white data-[state=inactive]:text-[#666666] data-[state=inactive]:hover:text-[#333333]",
      className
    )}
    {...props}
  />
))
TabsTrigger.displayName = TabsPrimitive.Trigger.displayName

const TabsContent = React.forwardRef<
  React.ElementRef<typeof TabsPrimitive.Content>,
  React.ComponentPropsWithoutRef<typeof TabsPrimitive.Content>
>(({ className, ...props }, ref) => (
  <TabsPrimitive.Content
    ref={ref}
    className={cn(
      "mt-4 ring-offset-background focus-visible:outline-none focus-visible:ring-2 focus-visible:ring-ring focus-visible:ring-offset-2",
      className
    )}
    {...props}
  />
))
TabsContent.displayName = TabsPrimitive.Content.displayName

export { Tabs, TabsList, TabsTrigger, TabsContent }

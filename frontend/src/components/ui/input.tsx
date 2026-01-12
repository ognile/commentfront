import * as React from "react"
import { cn } from "@/lib/utils"

const Input = React.forwardRef<HTMLInputElement, React.InputHTMLAttributes<HTMLInputElement>>(
  ({ className, type, ...props }, ref) => {
    return (
      <input
        type={type}
        className={cn(
          "flex h-10 w-full rounded-full border border-[rgba(0,0,0,0.1)] bg-white px-4 py-2 text-sm text-[#111111] ring-offset-background file:border-0 file:bg-transparent file:text-sm file:font-medium placeholder:text-[#999999] focus-visible:outline-none focus-visible:border-[#333333] disabled:cursor-not-allowed disabled:opacity-50 transition-colors",
          className
        )}
        ref={ref}
        {...props}
      />
    )
  }
)
Input.displayName = "Input"

export { Input }

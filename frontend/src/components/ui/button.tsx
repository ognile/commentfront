import * as React from "react"
import { Slot } from "@radix-ui/react-slot"
import { cva, type VariantProps } from "class-variance-authority"

import { cn } from "@/lib/utils"

const buttonVariants = cva(
  "inline-flex items-center justify-center whitespace-nowrap text-sm font-medium ring-offset-background transition-all focus-visible:outline-none focus-visible:ring-2 focus-visible:ring-ring focus-visible:ring-offset-2 disabled:pointer-events-none disabled:opacity-50",
  {
    variants: {
      variant: {
        default: "rounded-full bg-[#333333] text-white hover:opacity-85",
        destructive: "rounded-full bg-[#ef4444] text-white hover:opacity-85",
        outline: "rounded-full border border-[rgba(0,0,0,0.1)] bg-white hover:border-[#333333] hover:text-[#333333]",
        secondary: "rounded-full bg-[rgba(51,51,51,0.08)] text-[#333333] hover:bg-[rgba(51,51,51,0.15)]",
        ghost: "rounded-full hover:bg-[rgba(51,51,51,0.08)] hover:text-[#333333]",
        link: "text-[#333333] underline-offset-4 hover:underline",
        success: "rounded-full bg-[#22c55e] text-white hover:opacity-85",
      },
      size: {
        default: "h-10 px-5 py-2",
        sm: "h-9 px-4",
        lg: "h-11 px-8",
        icon: "h-10 w-10",
      },
    },
    defaultVariants: {
      variant: "default",
      size: "default",
    },
  }
)

export interface ButtonProps
  extends React.ButtonHTMLAttributes<HTMLButtonElement>,
    VariantProps<typeof buttonVariants> {
  asChild?: boolean
}

const Button = React.forwardRef<HTMLButtonElement, ButtonProps>(
  ({ className, variant, size, asChild = false, ...props }, ref) => {
    const Comp = asChild ? Slot : "button"
    return (
      <Comp
        className={cn(buttonVariants({ variant, size, className }))}
        ref={ref}
        {...props}
      />
    )
  }
)
Button.displayName = "Button"

export { Button, buttonVariants }

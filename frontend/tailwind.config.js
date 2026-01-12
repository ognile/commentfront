import tailwindAnimate from "tailwindcss-animate"

/** @type {import('tailwindcss').Config} */
export default {
  content: [
    './pages/**/*.{ts,tsx}',
    './components/**/*.{ts,tsx}',
    './app/**/*.{ts,tsx}',
    './src/**/*.{ts,tsx}',
  ],
  theme: {
    container: {
      center: true,
      padding: "2rem",
      screens: {
        "2xl": "1400px",
      },
    },
    extend: {
      colors: {
        // shadcn compatibility - using hsl variables
        border: "hsl(var(--border))",
        input: "hsl(var(--input))",
        ring: "hsl(var(--ring))",
        background: "hsl(var(--background))",
        foreground: "hsl(var(--foreground))",
        primary: {
          DEFAULT: "hsl(var(--primary))",
          foreground: "hsl(var(--primary-foreground))",
        },
        secondary: {
          DEFAULT: "hsl(var(--secondary))",
          foreground: "hsl(var(--secondary-foreground))",
        },
        destructive: {
          DEFAULT: "hsl(var(--destructive))",
          foreground: "hsl(var(--destructive-foreground))",
        },
        muted: {
          DEFAULT: "hsl(var(--muted))",
          foreground: "hsl(var(--muted-foreground))",
        },
        accent: {
          DEFAULT: "hsl(var(--accent))",
          foreground: "hsl(var(--accent-foreground))",
        },
        popover: {
          DEFAULT: "hsl(var(--popover))",
          foreground: "hsl(var(--popover-foreground))",
        },
        card: {
          DEFAULT: "hsl(var(--card))",
          foreground: "hsl(var(--card-foreground))",
        },
        // Pearl theme custom colors
        pearl: {
          bg: "#faf9f8",
          card: "#ffffff",
          border: "rgba(0, 0, 0, 0.1)",
          "border-strong": "rgba(0, 0, 0, 0.15)",
          accent: "#333333",
          "accent-soft": "rgba(51, 51, 51, 0.08)",
        },
        "text-pearl": {
          primary: "#111111",
          secondary: "#666666",
          tertiary: "#999999",
        },
        success: {
          DEFAULT: "#22c55e",
          soft: "rgba(34, 197, 94, 0.1)",
        },
        error: {
          DEFAULT: "#ef4444",
          soft: "rgba(239, 68, 68, 0.1)",
        },
        warning: {
          DEFAULT: "#f59e0b",
          soft: "rgba(245, 158, 11, 0.1)",
        },
        info: {
          DEFAULT: "#3b82f6",
          soft: "rgba(59, 130, 246, 0.1)",
        },
      },
      borderRadius: {
        lg: "var(--radius)",
        md: "calc(var(--radius) - 2px)",
        sm: "calc(var(--radius) - 4px)",
        card: "16px",
        pill: "9999px",
      },
      keyframes: {
        "accordion-down": {
          from: { height: 0 },
          to: { height: "var(--radix-accordion-content-height)" },
        },
        "accordion-up": {
          from: { height: "var(--radix-accordion-content-height)" },
          to: { height: 0 },
        },
        "pulse-subtle": {
          "0%": { transform: "scale(1)", opacity: "0.3" },
          "100%": { transform: "scale(1.8)", opacity: "0" },
        },
      },
      animation: {
        "accordion-down": "accordion-down 0.2s ease-out",
        "accordion-up": "accordion-up 0.2s ease-out",
        "pulse-subtle": "pulse-subtle 2s ease-out infinite",
      },
    },
  },
  plugins: [tailwindAnimate],
}

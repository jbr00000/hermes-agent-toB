import type { Config } from 'tailwindcss'

export default {
  content: ['./index.html', './src/**/*.{ts,tsx}'],
  theme: {
    extend: {
      fontFamily: {
        sans: ['"Microsoft YaHei"', '"PingFang SC"', 'Arial', 'sans-serif'],
      },
      colors: {
        shell: '#f5f6f7',
        panel: '#ffffff',
        ink: '#202326',
        line: '#dedfe3',
        field: '#f0f2f4',
        success: '#1f8a5b',
        caution: '#b7791f',
        danger: '#b42318',
        info: '#2f6f8f',
      },
      boxShadow: {
        panel: '0 10px 28px rgba(31, 35, 40, 0.08)',
      },
    },
  },
  plugins: [],
} satisfies Config

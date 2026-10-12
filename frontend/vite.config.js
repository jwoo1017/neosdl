import react from '@vitejs/plugin-react'
import { defineConfig } from 'vite'

// https://vite.dev/config/
export default defineConfig({
  plugins: [react()],
  server: {
    // 프론트(5173)와 백엔드(8000)를 브라우저 입장에서 같은 오리진으로
    // 보이게 한다 — 운영의 nginx 리버스 프록시와 동일한 구조를 로컬에서도
    // 재현해서 CORS/쿠키 설정 없이 바로 돌아가게 한다.
    proxy: {
      '/api': { target: 'http://localhost:8000', changeOrigin: true, ws: true },
    },
  },
})

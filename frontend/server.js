import { serve } from '@hono/node-server'
import handler from './dist/server/index.js'

const port = parseInt(process.env.PORT || '3000', 10)
const hostname = process.env.HOST || '0.0.0.0'

console.log(`Starting TerraPlan frontend server on ${hostname}:${port}`)

serve({
  fetch: handler.default || handler,
  port,
  hostname,
}, (info) => {
  console.log(`Server is running on http://${info.address}:${info.port}`)
})

import { createClient } from '@supabase/supabase-js'

const url = import.meta.env.VITE_SUPABASE_URL
const key = import.meta.env.VITE_SUPABASE_PUBLISHABLE_KEY

export const supabase = url && key ? createClient(url, key) : null
export const supabaseConfigured = Boolean(supabase)

export async function accessToken() {
  if (!supabase) return undefined
  const { data } = await supabase.auth.getSession()
  return data.session?.access_token
}

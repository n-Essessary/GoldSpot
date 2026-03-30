/**
 * Нормализует сырое имя сервера из API в { slug, label }.
 *
 * Примеры:
 *   "(EU) #Anniversary - Spineshatter" → { slug: "spineshatter", label: "Spineshatter (EU)" }
 *   "(US) Classic - Faerlina"          → { slug: "faerlina",     label: "Faerlina (US)" }
 *   "firemaw"                          → { slug: "firemaw",      label: "firemaw" }
 *
 * Формат FunPay: "(РЕГИОН) ... - Имя"
 * Fallback: raw строка как есть, lowercase для slug.
 *
 * @param {string | undefined | null} raw
 * @returns {{ slug: string, label: string }}
 */
export function normalizeServer(raw) {
  if (!raw) return { slug: '', label: '' }

  // Формат FunPay: "(РЕГИОН) любой текст - Имя сервера"
  // Жадный .*- берёт до ПОСЛЕДНЕГО тире → корректно для имён вроде "Spine-shatter"
  const m = raw.match(/^\((\w+)\)\s*.*-\s*(.+)$/)
  if (m) {
    const region = m[1].trim()   // "EU"
    const name   = m[2].trim()   // "Spineshatter"
    return {
      slug:  name.toLowerCase(), // "spineshatter"
      label: `${name} (${region})`, // "Spineshatter (EU)"
    }
  }

  // Fallback: название без региона
  const name = raw.trim()
  return {
    slug:  name.toLowerCase(),
    label: name,
  }
}

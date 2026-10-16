import fs from 'node:fs'

// Some Windows environments deny native realpath for readable project files.
// Vite uses the native variant, so fall back to Node's regular resolver only then.
if (process.platform === 'win32') {
  const nativeRealpath = fs.realpathSync.native
  fs.realpathSync.native = (path, options) => {
    try {
      return nativeRealpath(path, options)
    } catch (error) {
      if (error?.code !== 'EPERM') throw error
      return fs.realpathSync(path, options)
    }
  }
}

const { build } = await import('vite')
await build()

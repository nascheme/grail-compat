project(
  'grail-compat',
  version: '0.1.0',
  meson_version: '>= 1.6.0',
  default_options: ['c_std=c11', 'buildtype=release', 'b_ndebug=if-release'],
)

py = import('python').find_installation(pure: false)

# The native PMap/PSet need CPython 3.14+.  Without them (older Python, no
# C compiler, or -Dnative=disabled) the package is pure Python.
native = get_option('native')
if py.language_version().version_compare('< 3.14')
  native = native.disable_auto_if(true)
  if native.enabled()
    error('the native backend requires Python 3.14 or newer')
  endif
endif

build_native = not native.disabled() and add_languages('c', native: false, required: native)
if build_native
  py.extension_module(
    '_pds_native',
    'src/grail/_c/pds.c',
    'src/grail/_c/grail_hamt.c',
    include_directories: include_directories('src/grail/_c'),
    install: true,
    subdir: 'grail',
  )
endif

py.install_sources(
  'src/grail/__init__.py',
  'src/grail/_atom.py',
  'src/grail/_core.py',
  'src/grail/_numvec.py',
  'src/grail/_pds.py',
  'src/grail/_vector.py',
  'src/grail/py.typed',
  subdir: 'grail',
  # One wheel location for the whole package: platlib with the extension.
  pure: not build_native,
)


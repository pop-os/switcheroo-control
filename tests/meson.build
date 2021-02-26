integration_test = find_program('integration-test')

envs = environment()
envs.set ('top_builddir', meson.build_root())

test('switcheroo-control-integration-test',
  integration_test,
  env: envs
  )

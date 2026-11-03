/** One case of tools/tests/fixtures/publish-bundles.json; its `about` says what each field holds. */
export type Case = {
  name: string
  about?: string
  applies_to?: readonly string[]
  files?: Readonly<Record<string, string>>
  binary?: Readonly<Record<string, string>>
  links?: Readonly<Record<string, string>>
  generate?: { files?: number; pad_to_bytes?: number }
  call?: Readonly<Record<string, unknown>>
  expect: 'send' | 'refuse'
  rules?: readonly string[]
  at?: readonly (readonly [string, number, number])[]
  sent?: readonly string[]
}

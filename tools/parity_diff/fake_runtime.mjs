// A fixed clock and random numbers for the parity harnesses (imported first, so it runs before the sources load).
const NOW = Date.UTC(2026, 10, 5, 6, 30);
const RealDate = Date;
class FakeDate extends RealDate { constructor(...a) { if (a.length === 0) super(NOW); else super(...a); } static now() { return NOW; } }
globalThis.Date = FakeDate;
Math.random = () => 0.123456789;
globalThis.File ??= class File { constructor(parts, name, opts) { this.name = name; this.size = 1; this.type = opts?.type ?? ''; } };

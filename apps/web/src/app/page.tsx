const foundations = [
  "검증 가능한 주거비 계산",
  "버전형 정책 적격성 판정",
  "근거가 연결된 후보 비교",
];

export default function HomePage() {
  return (
    <main className="min-h-screen bg-slate-950 px-6 py-16 text-slate-50">
      <section className="mx-auto flex max-w-5xl flex-col gap-10">
        <div className="max-w-3xl space-y-5">
          <p className="text-sm font-semibold tracking-[0.2em] text-emerald-300">HOMEFIT AI</p>
          <h1 className="text-4xl font-bold tracking-tight sm:text-6xl">
            집을 찾는 것에서, 결정하는 것까지
          </h1>
          <p className="text-lg leading-8 text-slate-300">
            청년의 소득, 주거 문서, 정책 조건과 통근비를 연결해 실제로 감당 가능한 집을 비교합니다.
          </p>
        </div>

        <div className="grid gap-4 md:grid-cols-3">
          {foundations.map((item, index) => (
            <article className="rounded-2xl border border-slate-800 bg-slate-900 p-6" key={item}>
              <p className="mb-8 text-sm text-emerald-300">0{index + 1}</p>
              <h2 className="text-xl font-semibold">{item}</h2>
            </article>
          ))}
        </div>

        <div className="rounded-2xl border border-amber-400/30 bg-amber-400/10 p-5 text-sm text-amber-100">
          현재는 개발 기반을 구성한 초기 화면입니다. 정책 추천, 대출 승인 또는 계약 안전을 보장하지
          않습니다.
        </div>
      </section>
    </main>
  );
}

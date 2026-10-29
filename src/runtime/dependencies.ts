/** Free module calls are bounded; a timeout does not cancel remote effects. */
export class DependencyTimeout extends Error {
  readonly stage: string;
  constructor(stage: string) {
    super(`Dependency timed out: ${stage}`);
    this.stage = stage;
  }
}
export async function callDependency<T>(
  stage: string,
  call: () => Promise<T>,
  milliseconds: number,
): Promise<T> {
  let timer: ReturnType<typeof setTimeout>;
  const deadline = new Promise<never>((_resolve, reject) => {
    timer = setTimeout(
      () => reject(new DependencyTimeout(stage)),
      milliseconds,
    );
  });
  try {
    return await Promise.race([Promise.resolve().then(call), deadline]);
  } finally {
    clearTimeout(timer!);
  }
}

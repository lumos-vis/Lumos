
import { Injectable } from '@angular/core';
import { Bin } from '../models/prior-belief';

const BIN_COUNT = 10;
const BALL_COUNT = 30;

@Injectable({ providedIn: 'root' })
export class BinningService {

  readonly binCount = BIN_COUNT;
  readonly ballCount = BALL_COUNT;

  computeBins(values: number[]): Bin[] {
    const min = values.reduce((a, b) => a < b ? a : b);
    const max = values.reduce((a, b) => a > b ? a : b);
    const step = (max - min) / BIN_COUNT;

    return Array.from({ length: BIN_COUNT }, (_, i) => {
      const lo = min + (i * step);
      const hi = min + ((i + 1) * step);
      // for each bin return a range of lo and hi, and a label like "20–60"
      return {
        lo,
        hi,
        label: `${this.fmt(lo, step)}–${this.fmt(hi, step)}`,
      };
    });
  }

  emptyBallCounts(): number[] {
    return new Array(BIN_COUNT).fill(0);
  }

  emptyCountsFor(n: number): number[] {
    return new Array(n).fill(0);
  }

  categoricalBins(categories: string[]): Bin[] {
    return categories.map((cat, i) => ({ lo: i, hi: i + 1, label: cat }));
  }
  // formatting the label of bin based on step size
  // step >= 10: no decimal places
  // step >= 1: 1 decimal place
  // step >= 0.1: 2 decimal places
  // step < 0.1: 3 decimal places
  
  private fmt(n: number, step: number): string {
    if (step >= 10)  return n.toFixed(0);
    if (step >= 1)   return n.toFixed(1);
    if (step >= 0.1) return n.toFixed(2);
    return n.toFixed(3);
  }
}

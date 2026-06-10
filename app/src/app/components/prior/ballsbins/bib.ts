import { Component, EventEmitter, Input, Output, OnInit, OnChanges, SimpleChanges } from '@angular/core';
import { Bin } from '../../../models/prior-belief';
import { BinningService } from 'src/app/services/binning.service';
@Component({
  selector: 'app-balls-into-bins',
  templateUrl: './bib.html',
  styleUrls: ['./bib.css']
})
export class BallsIntoBinsComponent implements OnInit, OnChanges {
  @Input() values: number[] = [];
  @Input() initialCounts: number[] = [];
  @Input() fixedBins: Bin[] = [];   // when set, skips computation from values
  @Output() countsChange = new EventEmitter<number[]>();

  bins: Bin[] = [];
  counts: number[] = [];

  constructor(public binningService: BinningService) {}

  ngOnInit() {
    if (this.fixedBins.length > 0) {
      this.bins = this.fixedBins;
    } else if (this.values.length > 0) {
      this.bins = this.binningService.computeBins(this.values);
    }
    const emptyLen = this.bins.length || this.binningService.binCount;
    this.counts = this.initialCounts.length > 0
      ? this.initialCounts.slice()
      : this.binningService.emptyCountsFor(emptyLen);
  }

  ngOnChanges(changes: SimpleChanges) {
    if (changes['initialCounts'] && !changes['initialCounts'].firstChange) {
      this.counts = this.initialCounts.slice();
    }
  }
  getRemaining(): number {
    return this.binningService.ballCount - this.counts.reduce((a, b) => a + b, 0);
  }

 increment(index: number) {
    if (this.getRemaining() === 0) return;
    this.counts = this.counts.map((c, i) => i === index ? c + 1 : c);
    this.countsChange.emit(this.counts);
  }

  decrement(index: number) {
    if (this.counts[index] === 0) return;
    this.counts = this.counts.map((c, i) => i === index ? c - 1 : c);
    this.countsChange.emit(this.counts);
  }

  setCount(index: number, value: number) {
    const delta = value - this.counts[index];
    if (delta > 0 && delta > this.getRemaining()) return;
    this.counts = this.counts.map((c, i) => i === index ? value : c);
    this.countsChange.emit(this.counts);
  }

}

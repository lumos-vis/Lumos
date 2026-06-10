import { Component, EventEmitter, Input, Output, HostListener, OnChanges } from '@angular/core';
import { Bin } from '../../../models/prior-belief';

@Component({
  selector: 'app-bin-column',
  templateUrl: './bincol.html',
  styleUrls: ['./bincol.css']
})
export class BinColumnComponent implements OnChanges {
  @Input() bin: Bin = { lo: 0, hi: 0, label: '' };
  @Input() count: number = 0;
  @Input() maxBalls: number = 30;
  @Input() remaining: number = 30;

  @Output() increment = new EventEmitter<void>();
  @Output() decrement = new EventEmitter<void>();
  @Output() setCount = new EventEmitter<number>();

  rows: number[] = [];
  dragging = false;

  isHeld: 'increment' | 'decrement' | null = null;
  private holdTimeout: any = null;
  private holdInterval: any = null;

  ngOnChanges() {
    this.rows = Array.from({ length: this.maxBalls }, (_, i) => this.maxBalls - i);
  }

  // fire once immediately, then repeat after a short delay while held
  startHold(action: 'increment' | 'decrement') {
    this.isHeld = action;
    this.fireAction(action);
    this.holdTimeout = setTimeout(() => {
      this.holdInterval = setInterval(() => this.fireAction(action), 80);
    }, 400);
  }

  // clean up timers when the button is released or the cursor leaves
  stopHold() {
    this.isHeld = null;
    clearTimeout(this.holdTimeout);
    clearInterval(this.holdInterval);
    this.holdTimeout = null;
    this.holdInterval = null;
  }

  private fireAction(action: 'increment' | 'decrement') {
    if (action === 'increment') this.increment.emit();
    else this.decrement.emit();
  }

  isFilled(row: number): boolean {
    return row <= this.count;
  }

  isClickable(row: number): boolean {
    return row < this.count || this.remaining >= (row - this.count);
  }

  onMouseDown(row: number) {
    this.dragging = true;
    this.trySet(row);
  }

  onMouseEnter(row: number) {
    if (this.dragging) this.trySet(row);
  }

  @HostListener('document:mouseup')
  onMouseUp() {
    this.dragging = false;
  }

  // click on a ball to set the count up until that row for a column
  private trySet(row: number) {
    if (!this.isClickable(row)) return;
    this.setCount.emit(row);
  }
}

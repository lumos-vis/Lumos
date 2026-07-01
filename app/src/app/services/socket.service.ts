// libraries
import { Injectable } from "@angular/core";
import { Socket } from "ngx-socket-io";
import { map } from "rxjs/operators";

@Injectable()
export class ChatService {
  constructor(private vizSocket: Socket) {}

  connectToSocket() {
    this.vizSocket.connect();
  }

  removeAllListenersAndDisconnectFromSocket() {
    this.vizSocket.removeAllListeners();
    this.vizSocket.disconnect();
  }

  sendMessageToSaveSessionLogs(data, participantId) {
    let payload = {
      data: data,
      participantId: participantId,
    };
    this.vizSocket.emit("on_session_end_page_level_logs", payload);
  }

  sendMessageToSaveLogs() {
    this.vizSocket.emit("on_save_logs", null);
  }

  sendMessageToRestartBiasComputation() {
    this.vizSocket.emit("on_reset_bias_computation", null);
  }

  sendInteractionResponse(payload) {
    this.vizSocket.emit("on_interaction", payload);
  }

  sendPriors(payload) {
    console.log('[socket] sendPriors emitting, connected=', (this.vizSocket as any).ioSocket?.connected, payload);
    this.vizSocket.emit("on_commit_priors", payload);
  }

  sendSelectedSubjects(payload) {
    this.vizSocket.emit("on_selected_subjects", payload);
  }

  getDisconnectEventResponse() {
    return this.vizSocket.fromEvent("disconnect").pipe(map((obj) => obj));
  }

  getConnectEventResponse() {
    return this.vizSocket.fromEvent("connect").pipe(map((obj) => obj));
  }

  getInteractionResponse() {
    return this.vizSocket
      .fromEvent("interaction_response")
      .pipe(map((obj) => obj));
  }

  getAttributeDistribution() {
    return this.vizSocket
      .fromEvent("attribute_distribution")
      .pipe(map((obj) => obj));
  }
}

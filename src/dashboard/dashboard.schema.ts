import { Prop, Schema, SchemaFactory } from '@nestjs/mongoose';
import mongoose, { Document } from 'mongoose';

export type DashboardDocument = Dashboard & Document;

@Schema()
export class Dashboard {
  @Prop({ required: true, unique: true })
  keyname: string;

  @Prop()
  name: string;

  @Prop({ required: true })
  show: boolean;

  @Prop()
  icon: string;

  @Prop()
  generatedWorkspaceId?: string;

  @Prop()
  deletedAt?: Date;

  @Prop({
    type: [{ type: mongoose.Schema.Types.ObjectId, ref: 'Section' }],
    default: [],
  })
  sections: mongoose.Types.ObjectId[];
}

export const DashboardSchema = SchemaFactory.createForClass(Dashboard);

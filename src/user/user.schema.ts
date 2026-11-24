// user.schema.ts
import { Prop, Schema, SchemaFactory } from '@nestjs/mongoose';
import { Document, Types } from 'mongoose';
import { DashboardAccessSchema } from './dashboard-access.schema'; // Ajusta la ruta según tu estructura

export type UserDocument = User & Document;

@Schema()
export class User {
  @Prop({ required: true, unique: true })
  username: string;

  @Prop({ required: true })
  password: string;

  @Prop({ required: true })
  profileType: string;

  @Prop({ type: [DashboardAccessSchema], default: [] })
  access: Array<{
    dashboard: Types.ObjectId;
    sections: Types.ObjectId[];
  }>;

  @Prop()
  refreshToken?: string;
}

export const UserSchema = SchemaFactory.createForClass(User);

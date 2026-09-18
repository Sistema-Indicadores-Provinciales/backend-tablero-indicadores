import { Module } from '@nestjs/common';
import { DashboardLifecycleModule } from '../dashboard/dashboard-lifecycle.module';
import { MongooseModule } from '@nestjs/mongoose';
import { SectionsController } from './sections.controller';
import { SectionsService } from './sections.service';
import { Section, SectionSchema } from './sections.schema';
import { Dashboard, DashboardSchema } from 'src/dashboard/dashboard.schema';

@Module({
  imports: [
    DashboardLifecycleModule,
    MongooseModule.forFeature([
      { name: Section.name, schema: SectionSchema },
    ]),
  ],
  controllers: [SectionsController],
  providers: [SectionsService],
})
export class SectionsModule {}

import type { Tag } from '../default';

export interface JobDetail {
    /**
    * @label ID
    */
    id?: number;
    title?: string;
    description?: string;
    company?: string;
    companyImage?: File | null;
    /**
    * @format url
    */
    companyUrl?: string | null;
    /**
    * @format url
    */
    resumeUrl?: string | null;
    buttonLink?: string;
    buttonText?: string;
    /**
    * @format date-time
    */
    createdAt?: string;
    tags?: Tag[];
}

export interface JobList {
    /**
    * @label ID
    */
    id?: number;
    title?: string;
    company?: string;
    companyImage?: File | null;
    buttonLink?: string;
    buttonText?: string;
    /**
    * @format date-time
    */
    createdAt?: string;
    tags?: Tag[];
}

export interface Tag {
    /**
    * @label ID
    */
    id?: number;
    name?: string;
    color?: string;
}

